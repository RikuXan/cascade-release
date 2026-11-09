# Specification

Design of the first version, as implemented. Requirements are numbered R1–R14 in
[requirements.md](requirements.md).

## Repository layout

```text
.github/workflows/image-chart.yml   reusable workflow (on: workflow_call), the component
.github/workflows/ci.yml            this repo's CI: lint, tests, dry-run against testing/
.github/workflows/release.yml       this repo's own release (tags-only family, R14)
.github/actionlint.yaml             actionlint exceptions
cliff.toml                          bundled git-cliff configuration (versioning rules, notes template)
scripts/plan                        planner: decides per family whether, what and where to release
scripts/publish-release             creates the git tag and the GitHub Release of one family
scripts/requirements.txt            git-cliff, pinned with hashes
tests/                              planner, publish-release and chart contract tests
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
caller grants them, for both events and even for a dry run that never uses them.

### Inputs

| Input | Default | Description |
| --- | --- | --- |
| `image` | required | Image repository, e.g. `ghcr.io/rikuxan/agent-workstation` |
| `image-paths` | required | Newline-separated git pathspecs that make up the image. Must cover everything the Docker build reads (Dockerfile, every `COPY` source), or changes there never release |
| `chart-path` | required | Chart directory, e.g. `charts/agent-workstation` |
| `chart-paths` | `<chart-path>/**` + `image-paths` | Pathspecs of the chart family. Including the image paths is the cascade (R3); overriding it is possible but breaks R3 |
| `context` | `.` | Docker build context |
| `dockerfile` | `Dockerfile` | Dockerfile path, relative to the repository root |
| `platforms` | `linux/amd64` | Build platforms. Any other value also sets up QEMU |
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
| `image-version` | Image version this run releases, or else the newest existing one. In a preview or dry run: the version it would release |
| `image-digest` | Digest of `image-version`, if this run built the image or released a chart; empty otherwise. In a dry run: a placeholder of zeros |
| `chart-released` | `true` if this run released a chart |
| `chart-version` | Chart version this run releases, or empty. In a preview or dry run: the version it would release |

### Modes

The plan job decides the mode of a run, and the other jobs follow it:

| Mode | When | Effect |
| --- | --- | --- |
| `release` | `push` to the default branch | Everything below |
| `dry-run` | `dry-run: true`, on any event | Builds and packages what is due; pushes, tags and releases nothing |
| `preview` | every other event, e.g. `pull_request` | Plan only |

## How the workflow reaches its own files

Calling a reusable workflow hands GitHub its YAML file and nothing else: the jobs run with the
*consumer's* repository checked out, so `scripts/` and `cliff.toml` are not there. Each job that
needs them downloads this repository at exactly the commit the called workflow came from:

```sh
gh api "repos/${{ job.workflow_repository }}/tarball/${{ job.workflow_sha }}" | tar --extract --gzip …
```

- `job.workflow_repository` and `job.workflow_sha` identify the *called* workflow; the `github.*`
  contexts describe the caller. A consumer that pins `@0.1.0` therefore gets the planner, the
  git-cliff configuration and the tool pins of `0.1.0`, with no second reference to keep in sync.
- The files land in `$RUNNER_TEMP/cascade-release`, outside the workspace, so they never end up in
  the consumer's Docker build context.
- The download uses the consumer's `GITHUB_TOKEN`, which can read its own repository and public
  ones. That is why this repository is public; see [Pitfalls](#pitfalls).

## Versioning rules

`cliff.toml` encodes R4:

- `[git]`: conventional commits only (`filter_unconventional = true`), and breaking commits are
  never filtered out (`protect_breaking_commits = true`).
- Commit parsers, first match wins:
  - `^feat` → "Features"
  - `^fix` → "Bug Fixes"
  - `^chore\(deps` → "Dependencies" (covers `chore(deps)` and `chore(deps-dev)`)
  - everything else → skipped
- Breaking commits of any type survive the skip rule. The notes template lists them under
  "Breaking Changes" and nowhere else.
- `[bump]`: `breaking_always_bump_major = false` (breaking bumps minor while `0.x`),
  `features_always_bump_minor = true`. Any other surviving commit bumps patch.
- Initial version per family: `<prefix>0.1.0`, passed as `GIT_CLIFF__BUMP__INITIAL_TAG`, because
  git-cliff's own initial tag carries no prefix.
- A family is **due** when at least one commit in its range survives the parsers. Commits that
  only touch other paths, or only have skipped types, release nothing.

`1.0.0` is meant to be reached by hand (R4); from then on breaking changes bump major. How that
step works is an [open point](#open-points).

## Planning

`scripts/plan` runs per family with prefix `P` and pathspecs `S`:

1. **Previous release:** the highest `^P\d+\.\d+\.\d+$` tag reachable from `HEAD`
   (`git tag --merged HEAD`, compared numerically). Tags on other branches don't count.
2. **Range:** `git rev-list --topo-order prev..HEAD -- S`, or the whole history if there is no
   `prev`. Empty range → not due. git alone decides which commits belong to the family, so `S`
   are git pathspecs, not git-cliff globs.
3. **Releasable commits and next version:** git-cliff exports its context for `prev..HEAD`
   without any path filter. The planner keeps the commits of step 2, pins `prev` as the previous
   release and feeds the context back (`--from-context`): once with `--bumped-version` for the
   version, once for the notes. git-cliff only parses, bumps and renders; it never looks for
   `prev` itself. See [Pitfalls](#pitfalls).
4. **Validation:** `next` matches the family pattern and sorts above `prev`; otherwise fail.
5. **Tag target:** the first commit of the range in step 2, **not `HEAD`**. A release tag
   therefore always sits on a commit inside the family's paths, which keeps it visible to
   path-filtered history.
6. **Chart app version:** the image version released in this run, or else the newest image tag
   reachable from `HEAD`. If neither exists, the chart cannot be released: fail.

The planner is a standalone Python script (standard library only) with two modes, `image-chart`
and `tags` (one family, used by the [self-release](#self-release)). It refuses a shallow clone.
Its output is machine-readable, so it can be tested without GitHub Actions:

- `key=value` lines on stdout: `due`, `previous`, `version`, `tag` and `target` per family
  (prefixed `image-` / `chart-`), plus `chart-app-version`
- `<family>-notes.md` and `summary.md` in `--output-dir`
- with `--github-output`, the same values and the notes appended to that file in the
  `$GITHUB_OUTPUT` format

## Jobs

One run, serialized per ref with a concurrency group (`cascade-release-<ref>`,
`cancel-in-progress: false`, R13):

1. **plan** (`contents: read`): checkout with `fetch-depth: 0`, install git-cliff from
   `scripts/requirements.txt`, run the planner, publish its outputs and write the step summary.
2. **image**, if the image family is due (`contents: write`, `packages: write`):
   1. build and push `<image>:<version>` with OCI labels `source`, `version` and `revision` (the
      tag target), and record the digest from the build step;
   2. `scripts/publish-release`: create and push the annotated tag `<image-tag-prefix><version>`
      on the planned target, then create the GitHub Release "image <version>" with the notes.
3. **chart**, if the chart family is due, after **image** has succeeded or been skipped
   (`contents: write`, `packages: write`):
   1. resolve the digest of the app version: from the image job, or else
      `crane digest <image>:<app-version>`;
   2. write tag and digest into the shipped keys of `<chart-path>/values.yaml` with yq. The step
      fails if a key is missing: a chart that does not implement the
      [contract](#chart-contract) would silently ignore the stamp;
   3. `helm package --dependency-update --version <chart version> --app-version <app version>`,
      check that the package's values carry the digest, then `helm push`;
   4. `scripts/publish-release`: tag `<chart-tag-prefix><version>` on the planned target and the
      GitHub Release "chart <version>", whose notes start with
      `<image>:<app-version>@<digest>`.

The chart release is marked as the repository's latest release and the image release is not:
every run ends with the chart, and the chart is what gets deployed.

Release notes travel from **plan** to the other jobs as job outputs. The planner caps them at
100 kB, below both the 125 000 characters of a GitHub Release body and the size of one
environment variable.

Tags are pushed only after their artifact is published (R12). Failures converge:

- **Between publish and tag:** an unreleased version stays in the registry. The re-run
  overwrites it with the same version, which is harmless because nothing references an untagged
  version.
- **In chart, after image has tagged:** the image release is valid. "Re-run failed jobs" repeats
  only the chart job with the same plan and digest. "Re-run all jobs" plans again, finds the
  image released and the chart still due, and resolves the digest with crane.
- **Between tag and GitHub Release:** `publish-release` skips a tag that already sits on the
  planned target and a release that already exists, so "Re-run failed jobs" completes the
  release. If the tag sits elsewhere, the plan is stale and the job fails.

Everything happens in this one run: tags pushed with `GITHUB_TOKEN` do not trigger other
workflows.

### Pull requests and dry runs

- **`pull_request`:** only **plan** runs, against the merge ref GitHub checks out. Its step
  summary shows, per family, the previous tag, the next one (or "no release"), the tag target and
  the rendered notes (R11). Nothing is built or pushed; consumers keep their own CI for build
  checks. The preview plans from the pull request's own commits; with squash merging, the squash
  commit's subject decides what is released.
- **`dry-run: true`:** **plan**, then **image** builds without pushing, and **chart** stamps a
  placeholder digest and packages without pushing. No tags, no releases. This repo's CI uses it
  against `testing/`.

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

- Both shipped keys must exist in `values.yaml` as empty strings; the release refuses to stamp a
  chart without them.
- An unreleased chart from a working tree has empty shipped keys and `appVersion: 0.0.0`, so a
  hand-installed dev chart must set the public `tag` + `digest`.
- `app.kubernetes.io/version` gets the version only: a digest does not fit the 63-character label
  limit.

`testing/charts/fixture` is a working copy of this contract.

## Release notes

One template for both families, sections in this order: Breaking Changes, Features, Bug Fixes,
Dependencies. Each entry shows the scope, the message and a link to the commit; a breaking entry
adds the text of its `BREAKING CHANGE:` footer. The chart's notes start with the image reference
they pin. The first release of a family lists the whole history, which is acceptable.

## Self-release

`.github/workflows/release.yml` runs the planner on this repository in `tags` mode (prefix `""`)
and creates tag and GitHub Release with `scripts/publish-release` (R14). On pull requests it only
previews. The family's paths are what a consumer runs when it pins a version:

```text
.github/workflows/image-chart.yml
scripts/**
cliff.toml
```

`ci.yml`, `release.yml`, the tests and the fixture are deliberately not part of it: a Renovate
bump of a CI-only action would otherwise release a version that changes nothing for consumers.

Consumers pin `@<version>`, and Renovate's `github-actions` manager updates the reference.

## Tooling

| Tool | Pinned in | Updated by |
| --- | --- | --- |
| Actions | commit SHA with a version comment in the workflows | Renovate (`helpers:pinGitHubActionDigestsToSemver`) |
| git-cliff | `scripts/requirements.txt`, PyPI wheel with hashes (`pip --require-hashes`) | Renovate (`pip_requirements`) |
| helm, crane | `version:` of their setup actions, with a `# renovate:` comment | Renovate (custom regex manager) |
| yq | the `mikefarah/yq` action, which pins its image by digest | Renovate (`github-actions`) |
| actionlint | `docker://rhysd/actionlint:<version>` in `ci.yml` | Renovate (`github-actions`) |

Renovate commits these as `chore(deps)`, so a bump inside the self-release paths releases a patch
of cascade-release.

## Testing

Run everything locally with `python3 -m unittest discover --start-directory tests`. It needs
`git`, `helm` and `git-cliff` (on `PATH`, or its path in `GIT_CLIFF`).

**Planner tests** (`tests/test_plan.py`) build throwaway repositories with scripted commits and
tags and assert the planner's output. Cases from the design:

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

Further cases cover a previous tag on a commit outside the family's paths, convergence after a
failed chart release, breaking changes from `1.0.0` on and through a footer, other prefixes
(`v` / `chart-v`), numeric version order, merge commits, the order of the note sections, the
summary, the `$GITHUB_OUTPUT` format, the notes cap, shallow clones and the `tags` mode.

**`tests/test_publish_release.py`** runs `scripts/publish-release` against a bare repository
with a stub `gh`: tag placement, re-runs and the stale-plan check.

**`tests/test_chart_contract.py`** renders the fixture chart with `helm template` in every
combination of shipped and public values.

**CI** (`ci.yml`, on pull requests): actionlint, shellcheck, the tests above, and a job that
calls `./.github/workflows/image-chart.yml` with `dry-run: true` against `testing/`. The fixture
uses tag prefixes that never get tags (`fixture-`, `fixture-chart-`), so every dry run plans a
first release; a final job asserts that the dry run really planned, built and packaged
`0.1.0`, because a dry run that plans nothing would pass without testing anything.

## Pitfalls

Lessons from the GitLab component this design follows, and from building this one. Each one
shaped a rule above.

- **git-cliff only sees a tag that sits on a commit surviving `--include-path`.** With the tag on
  `HEAD` after a CI-only merge, the previous release becomes invisible, older commits count as
  unreleased again, and versions inflate with empty notes. Hence: the planner picks the range and
  pins `prev` itself, and tags go on the newest family commit.
- **git-cliff ≥ 2.14:** older versions lose the commit type in `--context` output. The pinned
  previous release must be a complete release object; the planner copies the shape from the
  exported context instead of hard-coding it.
- **The first prefixed release needs a prefixed initial tag**, otherwise the first chart version
  comes out as `0.1.0` and fails the `chart-` pattern.
- **Stamping the public `image.tag` / `image.digest`** lets a deployment's tag override run under
  the stamped digest. Hence the shipped keys and the pair rule.
- **Full clone:** git-cliff needs all history, the tags and the blobs; no shallow or `blob:none`
  checkout.
- **A private reusable workflow cannot read its own repository.** Probed on 2026-11-09 from
  agent-workstation with *Access* set to "repositories owned by RikuXan": the call itself works
  and `job.workflow_sha` resolves, but fetching cascade-release with the consumer's
  `GITHUB_TOKEN` ends in "Repository not found". The access setting covers the workflow file
  and actions referenced with a static `uses:`, not the token. Hence the public repository.
- **actionlint 1.7.12 does not know `job.workflow_*`** and reports them as undefined;
  `.github/actionlint.yaml` ignores exactly that message for the reusable workflow.
- **The `mikefarah/yq` action re-quotes its `cmd`** for `docker run`, which breaks
  backslash-escaped quotes (`\"`). The stamp command uses single-quoted yq expressions that read
  the key paths and values from the environment (`eval(strenv(…))`).
- **ghcr package access:** a package first pushed by a repo's Actions is linked automatically.
  One created earlier by another workflow or by hand needs the consumer repo under
  *Package settings → Manage Actions access*, with write.

## Decisions on the former open questions

- **Planner language:** Python, standard library only, tested with `unittest`. The context
  round-trip through git-cliff is JSON handling, which outgrew shell.
- **git-cliff installation:** the PyPI wheel, pinned with hashes in `scripts/requirements.txt`
  and installed into a throwaway virtualenv. It is checksum-verified and Renovate can update
  version and hashes together.
- **Preview:** step summary only. No pull request comment, so no `pull-requests: write`.

## Open points

- **Reaching `1.0.0` by hand is not designed yet.** A hand-pushed `1.0.0` tag has no image
  behind it, so the next chart-only release would look for `<image>:1.0.0` and fail. Needs an
  explicit path before anyone uses it.
- **Hand-pushed tags in general** bypass the planner's guarantees: a chart tag pushed past
  unreleased image commits makes the next image release come without a chart.
- **Concurrency of a called workflow:** GitHub does not document how a top-level `concurrency`
  group in a called workflow holds its jobs together. It is defence in depth here: an
  interleaved second run either converges on the same release or fails on a stale plan.
- **Platforms other than `linux/amd64`** set up QEMU but have never been built.
