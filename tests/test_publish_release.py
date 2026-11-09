import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PUBLISH = Path(__file__).resolve().parent.parent / "scripts" / "publish-release"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
}

FAKE_GH = """#!/usr/bin/env bash
state=$(dirname "$0")/releases
mkdir -p "$state"
case "$1 $2" in
  "release view") [ -e "$state/$3" ] ;;
  "release create") printf '%s\\n' "$@" > "$state/$3" ;;
  *) exit 64 ;;
esac
"""


class PublishRelease(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="cascade-release-test-"))
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.origin = self.workdir / "origin.git"
        self.repo = self.workdir / "repo"
        self.bin = self.workdir / "bin"
        self.bin.mkdir()
        (self.bin / "gh").write_text(FAKE_GH)
        (self.bin / "gh").chmod(0o755)
        self.notes = self.workdir / "notes.md"
        self.notes.write_text("### Features\n")
        self.env = {**os.environ, **GIT_ENV, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}

        self.git("init", "--quiet", "--bare", "--initial-branch", "main", str(self.origin), cwd=self.workdir)
        self.git("clone", "--quiet", str(self.origin), str(self.repo), cwd=self.workdir)
        self.first = self.commit("feat: first")
        self.second = self.commit("ci: second")
        self.git("push", "--quiet", "origin", "main")

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd or self.repo, env=self.env, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, message: str) -> str:
        self.git("commit", "--quiet", "--allow-empty", "--message", message)
        return self.git("rev-parse", "HEAD")

    def publish(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(PUBLISH), *args], cwd=self.repo, env=self.env, capture_output=True, text=True
        )

    def released(self, tag: str) -> list[str]:
        return (self.bin / "releases" / tag).read_text().splitlines()

    def test_tags_the_target_and_creates_the_release(self):
        result = self.publish("chart-0.1.0", self.first, "chart 0.1.0", str(self.notes), "true")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "chart-0.1.0^{commit}", cwd=self.origin), self.first)
        self.assertEqual(self.git("cat-file", "-t", "chart-0.1.0", cwd=self.origin), "tag")
        self.assertEqual(
            self.released("chart-0.1.0"),
            ["release", "create", "chart-0.1.0", "--verify-tag", "--title", "chart 0.1.0",
             "--notes-file", str(self.notes), "--latest=true"],
        )

    def test_latest_defaults_to_false(self):
        self.publish("0.1.0", self.first, "image 0.1.0", str(self.notes))

        self.assertEqual(self.released("0.1.0")[-1], "--latest=false")

    def test_rerun_after_the_tag_was_pushed_still_creates_the_release(self):
        self.git("tag", "--annotate", "0.1.0", "--message", "image 0.1.0", self.first)
        self.git("push", "--quiet", "origin", "refs/tags/0.1.0")

        result = self.publish("0.1.0", self.first, "image 0.1.0", str(self.notes))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already exists", result.stdout)
        self.assertEqual(self.released("0.1.0")[2], "0.1.0")

    def test_rerun_after_the_release_exists_changes_nothing(self):
        self.publish("0.1.0", self.first, "image 0.1.0", str(self.notes))
        (self.bin / "releases" / "0.1.0").write_text("untouched\n")

        result = self.publish("0.1.0", self.first, "image 0.1.0", str(self.notes))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.released("0.1.0"), ["untouched"])

    def test_stale_plan_is_rejected(self):
        self.publish("0.1.0", self.first, "image 0.1.0", str(self.notes))

        result = self.publish("0.1.0", self.second, "image 0.1.0", str(self.notes))

        self.assertEqual(result.returncode, 1)
        self.assertIn("The plan is stale", result.stderr)
        self.assertEqual(self.git("rev-parse", "0.1.0^{commit}", cwd=self.origin), self.first)


if __name__ == "__main__":
    unittest.main()
