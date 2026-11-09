# Specification

Intended design for the first version. Requirements are numbered R1–R14 in
[requirements.md](requirements.md).

## Repository layout

```text
.github/workflows/image-chart.yml   reusable workflow (on: workflow_call), the component
.github/workflows/ci.yml            this repo's CI: lint, planner tests, dry-run against testing/
.github/workflows/release.yml       this repo's own release (tags-only family, R14)
cliff.toml                          bundled git-cliff configuration (versioning rules, notes template)
scripts/plan                        planner: decides per family whether, what and where to release
tests/                              planner tests against throwaway git repositories
testing/                            fixture consumer: Dockerfile, rootfs/, charts/fixture/
docs/                               this specification
renovate.json                       pins for actions and tools
```

## Interface

### Caller

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
      contents: write   # tags and GitHub Releases
      packages: write   # ghcr.io image and chart
    with:
      image: ghcr.io/rikuxan/agent-workstation
      image-paths: |
        Dockerfile
        rootfs/**
      chart-path: charts/agent-workstation
```

The same call serves both events: on `pull_request` it only plans and previews (R11), on a push
to the default branch it releases (R6). A reusable workflow cannot raise permissions, so the
caller grants them.

### Inputs

| Input | Default | Description |
| --- | --- | --- |
| `image` | required | Image repository, e.g. `ghcr.io/rikuxan/agent-workstation` |
| `image-paths` | required | Newline-separated git pathspecs that make up the image. Must cover everything the Docker build reads (Dockerfile, every `COPY` source), or changes there never release |
| `chart-path` | required | Chart directory, e.g. `charts/agent-workstation` |
| `chart-paths` | `<chart-path>/**` + `image-paths` | Pathspecs of the chart family. Including the image paths is the cascade (R3); overriding it is possible but breaks R3 |
| `context` | `.` | Docker build context |
| `dockerfile` | `Dockerfile` | Dockerfile path |
| `platforms` | `linux/amd64` | Build platforms |
| `chart-registry` | `oci://ghcr.io/<lowercased owner>/charts` | OCI registry the chart is pushed to |
| `image-tag-prefix` | `""` | Git tag prefix of the image family (R5) |
| `chart-tag-prefix` | `chart-` | Git tag prefix of the chart family (R5) |
| `shipped-tag-key` | `.image.shipped.tag` | yq path that receives the released image tag (R8) |
| `shipped-digest-key` | `.image.shipped.digest` | yq path that receives the released image digest (R8) |
| `dry-run` | `false` | Build and package everything, push and tag nothing; used by this repo's CI |

Inputs that only calcium needs (`build-args` with a version placeholder, `lfs`) arrive with its
migration; see [consumers.md](consumers.md#calcium).

### Outputs

| Output | Description |
| --- | --- |
| `image-released` | `true` if this run released an image |
| `image-version` | Released image version, or the newest existing one |
| `image-digest` | Digest of `image-version` |
| `chart-released` | `true` if this run released a chart |
| `chart-version` | Released chart version, or empty |

## Versioning rules

`cliff.toml` encodes R4. Semantics, not a literal file:

- `[git]`: conventional commits only (`filter_unconventional = true`), and breaking commits are
  never filtered out (`protect_breaking_commits = true`).
- Commit parsers, first match wins:
  - breaking commits of any type → group "Breaking Changes"
  - `^feat` → "Features"
  - `^fix` → "Bug Fixes"
  - `^chore\(deps` → "Dependencies" (covers `chore(deps)` and `chore(deps-dev)`)
  - everything else → skipped
- `[bump]`: `breaking_always_bump_major = false` (breaking bumps minor while `0.x`),
  `features_always_bump_minor = true`. Any other surviving commit bumps patch.
- Initial version per family: `<prefix>0.1.0`, passed as `GIT_CLIFF__BUMP__INITIAL_TAG`, because
  git-cliff's own initial tag carries no prefix.
- A family is **due** when at least one commit in its range survives the parsers. Commits that
  only touch other paths, or only have skipped types, release nothing.

`1.0.0` is reached by pushing that tag by hand (R4); from then on breaking changes bump major.

## Planning

`scripts/plan` runs per family with prefix `P` and pathspecs `S`:

1. **Previous release:** the highest `^P\d+\.\d+\.\d+$` tag reachable from `HEAD`
   (`git tag --merged HEAD`, version-sorted). Tags on other branches don't count.
2. **Range:** `prev..HEAD` (or the whole history if there is no `prev`), restricted to `S`:
   `git rev-list prev..HEAD -- S`. Empty range → not due.
3. **Releasable commits and next version:** git-cliff over exactly that range and those paths
   (`--include-path` per pathspec plus the explicit range; git-cliff ≥ 2.14 combines them
   correctly), `--bumped-version` for the version, `--unreleased --tag <next>` for the notes.
   The planner must not rely on git-cliff finding `prev` by itself; see
   [Pitfalls](#pitfalls).
4. **Validation:** `next` matches the family pattern and sorts above `prev`; otherwise fail.
5. **Tag target:** the newest commit of the range, `git rev-list -n1 prev..HEAD -- S`, **not
   `HEAD`**. A release tag therefore always sits on a commit inside the family's paths, which
   keeps it visible to path-filtered history.
6. **Chart app version:** the image version released in this run, or else the newest image tag
   reachable from `HEAD`. If neither exists, the chart cannot be released: fail.

The planner is a standalone script with machine-readable output (e.g. `key=value` lines for
`$GITHUB_OUTPUT` plus notes files), so it can be tested without GitHub Actions.

## Jobs

On a push to the default branch, in one run, serialized per repository with a concurrency group
(`cancel-in-progress: false`, R13):

1. **plan** (`contents: read`): checkout with `fetch-depth: 0` and tags, install git-cliff, run
   the planner, write the outputs and a step summary.
2. **image**, if the image family is due (`contents: write`, `packages: write`):
   1. build and push `<image>:<version>` with OCI labels `source`, `version` and `revision`, and
      record the digest from the build step;
   2. create and push the annotated tag `<image-tag-prefix><version>` on the planned target;
   3. create the GitHub Release "image <version>" with the notes.
3. **chart**, if the chart family is due, after **image** has succeeded or been skipped
   (`contents: write`, `packages: write`):
   1. resolve the app version (planner) and its digest: from the image job, or else
      `crane digest <image>:<app-version>`;
   2. write both into the shipped keys of `<chart-path>/values.yaml` with yq;
   3. `helm package --version <chart version> --app-version <app version>`, then `helm push`;
   4. create and push the tag `<chart-tag-prefix><version>` on the planned target;
   5. create the GitHub Release "chart <version>" with the notes, naming
      `<image>:<app-version>@<digest>`.

Tags are pushed only after their artifact is published (R12). A failure between publish and tag
leaves an unreleased version in the registry; re-running overwrites it with the same version,
which is harmless because nothing references an untagged version. A failure in **chart** after
**image** has tagged leaves a valid image release; the re-run finds the image released and the
chart still due, and converges.

Everything happens in this one run: tags pushed with `GITHUB_TOKEN` do not trigger other
workflows.

### Pull requests and dry runs

- **`pull_request`:** only **plan** runs, against the merge ref GitHub checks out. Its step
  summary shows, per family, `prev → next` (or "no release") and the rendered notes (R11).
  Nothing is built or pushed; consumers keep their own CI for build checks.
- **`dry-run: true`:** **plan**, then **image** builds without pushing, and **chart** packages
  with a placeholder digest without pushing. No tags, no releases. This repo's CI uses it against
  `testing/`.

## Chart contract

A consumer chart reads the image like this (R9). Helm merges chart defaults and deployment values
into one tree, so a template cannot tell a stamped value from an override. Hence the separate
`shipped` keys that only the release writes.

```yaml
# values.yaml
image:
  repository: ghcr.io/rikuxan/agent-workstation
  # Override as a pair or not at all: setting either one replaces the shipped image.
  tag: ""
  digest: ""
  # Written by cascade-release when the chart is packaged; never set by hand.
  shipped:
    tag: ""
    digest: ""
```

```gotemplate
{{- define "agent-workstation.image" -}}
{{- $image := .Values.image -}}
{{- $tag := $image.tag -}}
{{- $digest := $image.digest -}}
{{- if and (not $tag) (not $digest) -}}
{{- $shipped := $image.shipped | default dict -}}
{{- $tag = $shipped.tag | default .Chart.AppVersion -}}
{{- $digest = $shipped.digest -}}
{{- end -}}
{{ $image.repository }}{{ with $tag }}:{{ . }}{{ end }}{{ with $digest }}@{{ . }}{{ end }}
{{- end }}
```

- An unreleased chart from a working tree has empty shipped keys and `appVersion: 0.0.0`, so a
  hand-installed dev chart must set the public `tag` + `digest`.
- `app.kubernetes.io/version` gets the version only: a digest does not fit the 63-character label
  limit.

## Release notes

One template for both families, sections in this order: Breaking Changes, Features, Bug Fixes,
Dependencies. Each entry shows the scope, the message and a link to the commit. The chart's notes
start with the image reference they pin. The first release of a family lists the whole history,
which is acceptable.

## Self-release

`.github/workflows/release.yml` runs the planner on this repository with one tags-only family
(prefix `""`, paths: `.github/workflows/**`, `scripts/**`, `cliff.toml`) and creates tag and
GitHub Release (R14). Consumers pin `@<version>`. Renovate's `github-actions` manager updates
reusable-workflow references, provided its token can read this private repository.

## Testing

**Planner tests** (`tests/`) build throwaway repositories with scripted commits and tags and
assert the planner's output. Cases:

| # | History | Expected |
| --- | --- | --- |
| 1 | no tags, mixed commits on both paths | image `0.1.0`, chart `chart-0.1.0` |
| 2 | `feat` on an image path | image minor, chart minor (cascade) |
| 3 | `chore(deps)` on the Dockerfile | image patch, chart patch |
| 4 | `fix` in the chart only | chart patch, no image; app version = newest image tag |
| 5 | only `docs` / `ci` / `refactor` commits | nothing due |
| 6 | `refactor!:` on an image path at `0.x` | image minor, chart minor |
| 7 | release, then a CI-only commit, then plan again | nothing due (tag-placement regression) |
| 8 | a higher tag exists only on another branch | ignored |
| 9 | chart due, no image tag anywhere | planner fails clearly |
| 10 | existing `0.4.2` / `chart-0.7.1`, `feat` on an image path | `0.5.0` / `chart-0.8.0` |

**CI** (`ci.yml`, on pull requests): actionlint, shellcheck, planner tests, and a job that calls
`./.github/workflows/image-chart.yml` with `dry-run: true` against `testing/`.

## Pitfalls

Lessons from the GitLab component this design follows. Each one shaped a rule above.

- **git-cliff only sees a tag that sits on a commit surviving `--include-path`.** With the tag on
  `HEAD` after a CI-only merge, the previous release becomes invisible, older commits count as
  unreleased again, and versions inflate with empty notes. Hence: explicit range from the
  planner's own `prev`, and tags placed on the newest family commit.
- **git-cliff ≥ 2.14:** older versions drop the range when `--include-path` is given and lose the
  commit type in `--context` output.
- **The first prefixed release needs a prefixed initial tag**, otherwise the first chart version
  comes out as `0.1.0` and fails the `chart-` pattern.
- **Stamping the public `image.tag` / `image.digest`** lets a deployment's tag override run under
  the stamped digest. Hence the shipped keys and the pair rule.
- **Full clone:** git-cliff needs all history, the tags and the blobs; no shallow or `blob:none`
  checkout.
- **ghcr package access:** a package first pushed by this repo's Actions is linked automatically.
  One created earlier by another workflow or by hand needs the consumer repo under
  *Package settings → Manage Actions access*, with write.
- **Private reusable workflow:** callers fail with a "workflow was not found" style error until
  this repo allows access to repositories owned by RikuXan.

## Open questions

- Planner language: POSIX sh with jq, or a small Python script if the logic outgrows shell. Tests
  follow the choice (bats or pytest).
- git-cliff installation: `orhun/git-cliff-action` or a checksum-verified binary download.
  Either way pinned and Renovate-managed.
- Preview: step summary only (current plan), or also a sticky pull request comment, which needs
  `pull-requests: write`.
