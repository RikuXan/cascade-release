import importlib.machinery
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLAN = ROOT / "scripts" / "plan"

IMAGE_PATHS = "Dockerfile\nrootfs/**"
CHART_PATHS = "charts/app/**\n" + IMAGE_PATHS

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}


class Result:
    def __init__(self, process: subprocess.CompletedProcess, output_dir: Path):
        self.returncode = process.returncode
        self.stderr = process.stderr
        self.values = dict(line.split("=", 1) for line in process.stdout.splitlines())
        self.output_dir = output_dir

    def file(self, name: str) -> str:
        return (self.output_dir / name).read_text()


class PlannerTestCase(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="cascade-release-test-"))
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.repo = self.workdir / "repo"
        self.repo.mkdir()
        self.clock = 1_700_000_000
        self.git("init", "--quiet", "--initial-branch", "main")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        self.clock += 60
        date = f"{self.clock} +0000"
        env = {**os.environ, **GIT_ENV, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        return subprocess.run(
            ["git", *args], cwd=cwd or self.repo, env=env, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, message: str, *paths: str) -> str:
        for path in paths:
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a") as handle:
                handle.write(f"{message}\n")
        self.git("add", "--all")
        self.git("commit", "--quiet", "--message", message)
        return self.git("rev-parse", "HEAD")

    def tag(self, name: str, commit: str = "HEAD") -> None:
        self.git("tag", "--annotate", name, "--message", name, commit)

    def plan(self, *args: str, repository: Path | None = None) -> Result:
        output_dir = Path(tempfile.mkdtemp(dir=self.workdir))
        env = {key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")}
        process = subprocess.run(
            [str(PLAN), "--repository", str(repository or self.repo), "--output-dir", str(output_dir), *args],
            env=env,
            capture_output=True,
            text=True,
        )
        return Result(process, output_dir)

    def plan_image_chart(self, *extra: str, **kwargs) -> Result:
        return self.plan(
            "image-chart", "--image-paths", IMAGE_PATHS, "--chart-paths", CHART_PATHS, *extra, **kwargs
        )

    def release(self, result: Result) -> None:
        for family in ("image", "chart"):
            if result.values[f"{family}-due"] == "true":
                self.tag(result.values[f"{family}-tag"], result.values[f"{family}-target"])

    def assert_plan(self, result: Result, image: str | None, chart: str | None, app_version: str = ""):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.values["image-tag"], image or "", result.values)
        self.assertEqual(result.values["image-due"], "true" if image else "false")
        self.assertEqual(result.values["chart-tag"], chart or "", result.values)
        self.assertEqual(result.values["chart-due"], "true" if chart else "false")
        self.assertEqual(result.values["chart-app-version"], app_version)


class SpecCases(PlannerTestCase):
    def seed(self, image: str = "0.4.2", chart: str = "chart-0.7.1") -> None:
        self.commit("feat: add the image", "Dockerfile")
        self.commit("feat(chart): add the chart", "charts/app/Chart.yaml")
        self.tag(image, "HEAD~1")
        self.tag(chart)

    def test_01_first_release_of_both_families(self):
        self.commit("feat: add the image", "Dockerfile", "rootfs/etc/motd")
        self.commit("docs: describe it", "README.md")
        chart_commit = self.commit("fix(chart): set the port", "charts/app/values.yaml")
        self.commit("chore: tidy", "rootfs/etc/motd")
        image_commit = self.git("rev-parse", "HEAD")
        self.commit("ci: lint", ".github/workflows/ci.yml")

        result = self.plan_image_chart()

        self.assert_plan(result, "0.1.0", "chart-0.1.0", "0.1.0")
        self.assertEqual(result.values["image-version"], "0.1.0")
        self.assertEqual(result.values["chart-version"], "0.1.0")
        self.assertEqual(result.values["image-previous"], "")
        self.assertEqual(result.values["image-target"], image_commit)
        self.assertEqual(result.values["chart-target"], image_commit)
        self.assertNotEqual(chart_commit, image_commit)

    def test_02_feature_on_an_image_path_cascades(self):
        self.seed()
        self.commit("feat(shell): add zsh", "rootfs/etc/zshrc")

        self.assert_plan(self.plan_image_chart(), "0.5.0", "chart-0.8.0", "0.5.0")

    def test_03_dependency_update_releases_a_patch(self):
        self.seed()
        self.commit("chore(deps): update the base image", "Dockerfile")

        result = self.plan_image_chart()

        self.assert_plan(result, "0.4.3", "chart-0.7.2", "0.4.3")
        self.assertIn("### Dependencies", result.file("image-notes.md"))

    def test_04_chart_only_fix_pins_the_newest_image(self):
        self.seed()
        self.tag("0.3.9", "HEAD~1")
        self.commit("fix(chart): quote the annotation", "charts/app/templates/deployment.yaml")

        result = self.plan_image_chart()

        self.assert_plan(result, None, "chart-0.7.2", "0.4.2")
        self.assertEqual(result.values["image-previous"], "0.4.2")
        self.assertEqual(result.file("image-notes.md"), "")

    def test_05_non_releasing_types_release_nothing(self):
        self.seed()
        self.commit("docs: explain the image", "rootfs/README.md")
        self.commit("ci: cache the build", "Dockerfile")
        self.commit("refactor(chart): reorder values", "charts/app/values.yaml")
        self.commit("chore: tidy", "rootfs/etc/motd")
        self.commit("update things without a type", "Dockerfile")

        self.assert_plan(self.plan_image_chart(), None, None)

    def test_06_breaking_change_bumps_minor_before_1_0(self):
        self.seed()
        self.commit("refactor!: rename the user", "rootfs/etc/passwd")

        result = self.plan_image_chart()

        self.assert_plan(result, "0.5.0", "chart-0.8.0", "0.5.0")
        self.assertIn("### Breaking Changes", result.file("image-notes.md"))
        self.assertIn("Rename the user", result.file("chart-notes.md"))

    def test_07_release_then_ci_only_commit_releases_nothing(self):
        self.seed()
        self.commit("feat: add tmux", "rootfs/etc/tmux.conf")
        self.commit("ci: lint", ".github/workflows/ci.yml")
        first = self.plan_image_chart()
        self.assert_plan(first, "0.5.0", "chart-0.8.0", "0.5.0")
        self.assertEqual(first.values["image-target"], self.git("rev-parse", "HEAD~1"))
        self.release(first)

        self.commit("ci: lint more", ".github/workflows/ci.yml")

        self.assert_plan(self.plan_image_chart(), None, None)

    def test_08_tags_on_other_branches_are_ignored(self):
        self.seed()
        self.git("switch", "--quiet", "--create", "other")
        self.commit("feat: experiment", "Dockerfile")
        self.tag("9.0.0")
        self.tag("chart-9.0.0")
        self.git("switch", "--quiet", "main")
        self.commit("fix: pin the shell", "Dockerfile")

        self.assert_plan(self.plan_image_chart(), "0.4.3", "chart-0.7.2", "0.4.3")

    def test_09_chart_due_without_any_image_fails(self):
        self.commit("build: add the image", "Dockerfile")
        self.commit("feat(chart): add the chart", "charts/app/Chart.yaml")

        result = self.plan_image_chart()

        self.assertEqual(result.returncode, 1)
        self.assertIn("no image for it to pin", result.stderr)
        self.assertEqual(result.values, {})

    def test_10_existing_versions_continue(self):
        self.seed()
        self.commit("feat: add ssh", "rootfs/etc/ssh/sshd_config")

        result = self.plan_image_chart()

        self.assert_plan(result, "0.5.0", "chart-0.8.0", "0.5.0")
        self.assertEqual(result.values["image-previous"], "0.4.2")
        self.assertEqual(result.values["chart-previous"], "0.7.1")


class Boundaries(PlannerTestCase):
    def test_previous_tag_on_a_commit_outside_the_family_paths(self):
        self.commit("feat: add the image", "Dockerfile")
        self.commit("feat(chart): add the chart", "charts/app/Chart.yaml")
        self.commit("ci: lint", ".github/workflows/ci.yml")
        self.tag("0.4.2")
        self.tag("chart-0.7.1")
        self.commit("docs: readme", "README.md")

        self.assert_plan(self.plan_image_chart(), None, None)

        self.commit("fix: pin the shell", "Dockerfile")
        result = self.plan_image_chart()

        self.assert_plan(result, "0.4.3", "chart-0.7.2", "0.4.3")
        self.assertNotIn("Add the image", result.file("image-notes.md"))

    def test_converges_after_a_failed_chart_release(self):
        self.commit("feat: add the image", "Dockerfile")
        self.commit("feat(chart): add the chart", "charts/app/Chart.yaml")
        first = self.plan_image_chart()
        self.assert_plan(first, "0.1.0", "chart-0.1.0", "0.1.0")
        self.tag(first.values["image-tag"], first.values["image-target"])

        self.assert_plan(self.plan_image_chart(), None, "chart-0.1.0", "0.1.0")

    def test_breaking_change_bumps_major_from_1_0(self):
        self.commit("feat: add the image", "Dockerfile")
        self.tag("1.2.3")
        self.tag("chart-0.7.1")
        self.commit("fix!: drop the legacy shell", "Dockerfile")

        self.assert_plan(self.plan_image_chart(), "2.0.0", "chart-0.8.0", "2.0.0")

    def test_breaking_change_footer(self):
        self.commit("feat: add the image", "Dockerfile")
        self.tag("0.4.2")
        self.tag("chart-0.7.1")
        self.commit("chore(deps): update debian\n\nBREAKING CHANGE: needs a newer kernel", "Dockerfile")

        result = self.plan_image_chart()

        self.assert_plan(result, "0.5.0", "chart-0.8.0", "0.5.0")
        self.assertIn("Update debian: needs a newer kernel", result.file("image-notes.md"))

    def test_prefixes_are_inputs(self):
        self.commit("feat: add the image", "Dockerfile")
        self.tag("v1.1.0")
        self.tag("chart-v2.1.0")
        self.tag("0.9.0")
        self.commit("fix: pin the shell", "Dockerfile")

        result = self.plan_image_chart("--image-tag-prefix", "v", "--chart-tag-prefix", "chart-v")

        self.assert_plan(result, "v1.1.1", "chart-v2.1.1", "1.1.1")
        self.assertEqual(result.values["image-version"], "1.1.1")

    def test_versions_sort_numerically(self):
        self.commit("feat: add the image", "Dockerfile")
        self.tag("0.9.0")
        self.tag("0.10.0")
        self.tag("chart-0.2.0")
        self.commit("fix: pin the shell", "Dockerfile")

        self.assert_plan(self.plan_image_chart(), "0.10.1", "chart-0.2.1", "0.10.1")

    def test_merge_commits(self):
        self.commit("feat: add the image", "Dockerfile")
        self.tag("0.4.2")
        self.tag("chart-0.7.1")
        self.git("switch", "--quiet", "--create", "feature")
        feature = self.commit("feat: add tmux", "rootfs/etc/tmux.conf")
        self.git("switch", "--quiet", "main")
        self.commit("ci: lint", ".github/workflows/ci.yml")
        self.git("merge", "--quiet", "--no-ff", "--message", "Merge branch 'feature'", "feature")

        result = self.plan_image_chart()

        self.assert_plan(result, "0.5.0", "chart-0.8.0", "0.5.0")
        self.assertEqual(result.values["image-target"], feature)
        self.release(result)
        self.assert_plan(self.plan_image_chart(), None, None)

    def test_notes_are_grouped_and_ordered(self):
        self.commit("feat: add the image", "Dockerfile")
        self.tag("0.4.2")
        self.tag("chart-0.7.1")
        self.commit("chore(deps): update debian", "Dockerfile")
        self.commit("fix(shell): source the profile", "rootfs/etc/profile")
        self.commit("feat(shell): add zsh", "rootfs/etc/zshrc")
        breaking = self.commit("feat(ssh)!: disable password logins", "rootfs/etc/ssh/sshd_config")
        self.commit("docs: mention zsh", "rootfs/README.md")

        notes = self.plan_image_chart().file("image-notes.md")

        headings = [line for line in notes.splitlines() if line.startswith("### ")]
        self.assertEqual(
            headings, ["### Breaking Changes", "### Features", "### Bug Fixes", "### Dependencies"]
        )
        self.assertIn(f"- **ssh:** Disable password logins (`{breaking[:7]}`)", notes)
        self.assertEqual(notes.count("Disable password logins"), 1)
        self.assertNotIn("Mention zsh", notes)

    def test_summary_previews_both_families(self):
        self.commit("feat: add the image", "Dockerfile")
        self.tag("0.4.2")
        self.tag("chart-0.7.1")
        self.commit("fix(chart): set the port", "charts/app/values.yaml")

        summary = self.plan_image_chart().file("summary.md")

        self.assertIn("| image | `0.4.2` | no release | |", summary)
        self.assertIn("| chart | `chart-0.7.1` | **`chart-0.7.2`** |", summary)
        self.assertIn("Pins image version `0.4.2`.", summary)
        self.assertIn("#### Bug Fixes", summary)

    def test_shallow_clone_is_rejected(self):
        self.commit("feat: add the image", "Dockerfile")
        self.commit("fix: pin the shell", "Dockerfile")
        shallow = self.workdir / "shallow"
        self.git("clone", "--quiet", "--depth", "1", self.repo.as_uri(), str(shallow), cwd=self.workdir)

        result = self.plan_image_chart(repository=shallow)

        self.assertEqual(result.returncode, 1)
        self.assertIn("shallow clone", result.stderr)


class Outputs(PlannerTestCase):
    def test_github_output_carries_values_and_multiline_notes(self):
        self.commit("feat: add the image", "Dockerfile")
        self.commit("fix: pin the shell", "Dockerfile")
        self.commit("feat(chart): add the chart", "charts/app/Chart.yaml")
        github_output = self.workdir / "github-output"

        result = self.plan_image_chart_with("--github-output", str(github_output))

        self.assertEqual(result.returncode, 0, result.stderr)
        parsed = parse_github_output(github_output.read_text())
        self.assertEqual(parsed["image-tag"], "0.1.0")
        self.assertEqual(parsed["chart-app-version"], "0.1.0")
        self.assertEqual(parsed["image-notes"], result.file("image-notes.md").rstrip("\n"))
        self.assertEqual(parsed["chart-notes"], result.file("chart-notes.md").rstrip("\n"))
        self.assertIn("### Bug Fixes", parsed["image-notes"])

    def plan_image_chart_with(self, *options: str) -> Result:
        return self.plan(
            *options, "image-chart", "--image-paths", IMAGE_PATHS, "--chart-paths", CHART_PATHS
        )

    def test_long_notes_are_capped_below_the_release_body_limit(self):
        module = load_planner()
        entry = "- **scope:** Etwas Schönes ist passiert (`abcdef0`)\n"
        total = module.NOTES_LIMIT // len(entry.encode()) + 50
        notes = "### Features\n\n" + entry * total

        capped = module.capped(notes.rstrip("\n"))

        self.assertLess(len(capped.encode()), module.NOTES_LIMIT + 100)
        kept = capped.count(entry.strip())
        self.assertGreater(kept, 0)
        self.assertTrue(
            capped.endswith(f"\n\n_{total - kept} more entries are not shown; see the commit history._"),
            capped[-80:],
        )
        self.assertEqual(module.capped("short"), "short")


def parse_github_output(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    lines = iter(text.splitlines())
    for line in lines:
        if "<<" in line.split("=", 1)[0]:
            key, delimiter = line.split("<<", 1)
            body = []
            for inner in lines:
                if inner == delimiter:
                    break
                body.append(inner)
            values[key] = "\n".join(body)
        else:
            key, value = line.split("=", 1)
            values[key] = value
    return values


def load_planner():
    loader = importlib.machinery.SourceFileLoader("cascade_release_plan", str(PLAN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


class TagsOnly(PlannerTestCase):
    PATHS = ".github/workflows/**\nscripts/**\ncliff.toml"

    def test_first_release(self):
        self.commit("feat: add the workflow", ".github/workflows/image-chart.yml")
        self.commit("docs: explain", "README.md")

        result = self.plan("tags", "--paths", self.PATHS)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.values["due"], "true")
        self.assertEqual(result.values["tag"], "0.1.0")
        self.assertEqual(result.values["target"], self.git("rev-parse", "HEAD~1"))
        self.assertIn("### Features", result.file("notes.md"))

    def test_docs_release_nothing(self):
        self.commit("feat: add the workflow", ".github/workflows/image-chart.yml")
        self.tag("0.1.0")
        self.commit("feat: describe the design", "docs/spec.md")
        self.commit("docs: comment the planner", "scripts/plan")

        result = self.plan("tags", "--paths", self.PATHS)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.values, {"due": "false", "previous": "0.1.0", "version": "", "tag": "", "target": ""})


if __name__ == "__main__":
    unittest.main()
