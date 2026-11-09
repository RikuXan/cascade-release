# cascade-release

Reusable GitHub Actions workflow that releases a container image and the Helm chart that ships it
as two independent version families, derived from conventional commits. Every image release
cascades into a chart release that pins the new image by digest; chart-only changes release only
the chart.

## Usage

```yaml
name: Release

on:
  push:
    branches: [main]
  pull_request:

jobs:
  release:
    uses: RikuXan/cascade-release/.github/workflows/image-chart.yml@0.1.0
    permissions:
      contents: write
      packages: write
    with:
      image: ghcr.io/rikuxan/agent-workstation
      image-paths: |
        Dockerfile
        rootfs/**
      chart-path: charts/agent-workstation
```

On a pull request this previews the versions and release notes a merge would produce. On a push
to the default branch it builds and pushes the image, packages and pushes the chart, and creates
a git tag and a GitHub Release for each. The chart has to implement the
[chart contract](docs/spec.md#chart-contract), and `Chart.yaml` keeps `0.0.0` placeholders.

## Documentation

| Document | Content |
| --- | --- |
| [docs/requirements.md](docs/requirements.md) | What the component has to do and why, non-goals, decisions and rejected alternatives |
| [docs/spec.md](docs/spec.md) | Design: interface, versioning rules, planning algorithm, jobs, chart contract, tooling, testing, pitfalls, open points |
| [docs/consumers.md](docs/consumers.md) | The first consumers (agent-workstation, calcium) and how each migrates |
| [testing/README.md](testing/README.md) | The fixture image and chart that CI dry-runs the workflow against |

## Development

```sh
python3 -m unittest discover --start-directory tests
```

The tests need `git`, `helm` and `git-cliff` (on `PATH`, or its path in `GIT_CLIFF`); the version
CI uses is pinned in `scripts/requirements.txt`. Pull requests also run actionlint, shellcheck
and a dry run of the whole workflow against `testing/`.
