import subprocess
import tempfile
import unittest
from pathlib import Path

CHART = Path(__file__).resolve().parent.parent / "testing" / "charts" / "fixture"
REPOSITORY = "ghcr.io/rikuxan/cascade-release-fixture"
SHIPPED = {"image.shipped.tag": "0.5.0", "image.shipped.digest": "sha256:" + "a" * 64}
OTHER_DIGEST = "sha256:" + "b" * 64


def rendered_image(chart: Path, values: dict[str, str]) -> str:
    command = ["helm", "template", "fixture", str(chart)]
    for key, value in values.items():
        command += ["--set-string", f"{key}={value}"]
    output = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    images = [line.split("image:", 1)[1].strip() for line in output.splitlines() if "image:" in line]
    assert len(images) == 1, output
    return images[0]


class ChartContract(unittest.TestCase):
    def test_released_chart_runs_the_shipped_pair(self):
        self.assertEqual(rendered_image(CHART, SHIPPED), f"{REPOSITORY}:0.5.0@{SHIPPED['image.shipped.digest']}")

    def test_tag_override_never_runs_under_the_shipped_digest(self):
        self.assertEqual(rendered_image(CHART, {**SHIPPED, "image.tag": "dev"}), f"{REPOSITORY}:dev")

    def test_digest_override_replaces_the_shipped_pair(self):
        self.assertEqual(
            rendered_image(CHART, {**SHIPPED, "image.digest": OTHER_DIGEST}), f"{REPOSITORY}@{OTHER_DIGEST}"
        )

    def test_public_pair_wins_as_a_unit(self):
        self.assertEqual(
            rendered_image(CHART, {**SHIPPED, "image.tag": "dev", "image.digest": OTHER_DIGEST}),
            f"{REPOSITORY}:dev@{OTHER_DIGEST}",
        )

    def test_unreleased_chart_falls_back_to_the_app_version(self):
        self.assertEqual(rendered_image(CHART, {}), f"{REPOSITORY}:0.0.0")

    def test_packaged_chart_without_shipped_tag_uses_its_app_version(self):
        with tempfile.TemporaryDirectory() as destination:
            subprocess.run(
                ["helm", "package", str(CHART), "--version", "0.8.0", "--app-version", "0.5.0",
                 "--destination", destination],
                check=True,
                capture_output=True,
            )
            package = Path(destination) / "fixture-0.8.0.tgz"
            self.assertEqual(rendered_image(package, {}), f"{REPOSITORY}:0.5.0")


if __name__ == "__main__":
    unittest.main()
