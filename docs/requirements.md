# Requirements

## Context

Several personal repositories ship a container image together with a Helm chart that deploys it,
and each one has grown its own release workflow:

- **agent-workstation** derives versions from a `major.minor` kept by hand in the Dockerfile
  (`ARG IMAGE_VERSION`) and in `Chart.yaml`, and stamps the patch at publish time.
- **calcium** releases from hand-pushed `v*` / `chart-v*` tags.

Both write the released image into the chart's public `image.tag` / `image.digest` values, and
both keep version bookkeeping in files or in people's heads. cascade-release replaces these with
one reusable workflow whose versions come from the commit history. The design follows a GitLab CI
component Philipp built at work for the same job (image + chart as two tag families via
git-cliff); the lessons learned there are folded into [spec.md](spec.md#pitfalls).

## Functional requirements

1. **Two release families per repository: image and chart.** Each has its own version, git tag,
   GitHub Release and release notes. An image release and a chart release are distinguishable at
   a glance, and a chart can be released without a new image.
2. **Versions come from conventional commits** since the family's previous release, restricted to
   the family's paths. No version number is maintained by hand in any file: the Dockerfile carries
   no version argument for releasing, and `Chart.yaml` keeps `version: 0.0.0` /
   `appVersion: "0.0.0"` placeholders.
3. **The image cascades into the chart.** The chart family's paths include the image family's
   paths, so every image release also releases a chart that pins the new image. Changes to chart
   files alone release only the chart.
4. **Release strategy "features, fixes, dependencies":**
   - `feat` → minor
   - `fix` → patch
   - `chore(deps…)` → patch, so Renovate's dependency bumps (Claude Code, herdr, kubectl, base
     images, …) actually ship
   - a breaking change of any type (`!` or `BREAKING CHANGE:` footer) → minor while the version is
     `0.x`, major from `1.0.0` on; reaching `1.0.0` is a deliberate, manual step
   - every other type (`docs`, `ci`, `test`, `refactor`, `style`, `build`, other `chore`) releases
     nothing
5. **Tag names:** image `X.Y.Z`, chart `chart-X.Y.Z`. The first release of each family is
   `0.1.0` / `chart-0.1.0`. Prefixes are inputs so other schemes stay possible.
6. **Release on push to the default branch**, whether from a merged pull request or a direct push.
   No release pull request, no manual tagging.
7. **The image** is built and pushed to the registry under its version as tag; the run records the
   pushed digest.
8. **The chart** is packaged with `--version <chart version>` and `--app-version <image version>`,
   with the released image tag and digest written into dedicated values keys
   (`.image.shipped.tag` / `.image.shipped.digest`) at package time, and pushed to an OCI
   registry. Nothing is committed back to git.
9. **Overrides stay predictable.** A deployment that sets the public `image.tag` / `image.digest`
   always wins over the shipped pair, and the public pair is taken as a unit (a tag override
   never runs under a shipped digest). See the chart contract in the spec.
10. **GitHub Releases** for both families, with grouped release notes (features, bug fixes,
    dependencies, breaking changes). The chart's notes name the image it pins.
11. **Pull request preview:** pull requests show which versions a merge would release and their
    notes, without publishing anything.
12. **No phantom releases:** a git tag exists only for an artifact that was published. A failed
    run can be re-run and converges.
13. **One release at a time** per repository.
14. **cascade-release releases itself** with the same versioning rules (a tags-only family), so
    consumers pin a version and Renovate updates it.

## Non-functional requirements

- **Private repository.** Consumers are Philipp's own repositories; the repo's
  *Settings → Actions → General → Access* must be "Accessible from repositories owned by
  RikuXan" before any consumer can call it.
- **Pinned tooling**, updated by Renovate: actions by version or digest, git-cliff (≥ 2.14), helm,
  yq, crane.
- **Testable without a consumer:** the planner is a script with its own tests, and the
  component's CI dry-runs the whole workflow against a fixture image and chart in this repo.
- **Small interface:** only inputs a known consumer needs. New inputs arrive with the consumer that
  needs them.

## Non-goals (for now)

- More than one image or more than one chart per repository.
- "Upstream version" mode (image tagged with the version of the software it packages).
- Release pull requests, changelog files committed to the repo, version bumps written to files.
- Automatic `1.0.0`.
- Registries other than ghcr.io via `GITHUB_TOKEN`.
- GitLab.

## Decisions and rejected alternatives

| Option | Verdict |
| --- | --- |
| **git-cliff** + own workflow | **Chosen.** One tool computes versions (`--bumped-version`, custom parsers, pre-1.0 knobs, `--tag-pattern`, `--include-path`) and renders the notes. Its include-path boundary bug is avoided by how tags are placed (spec). |
| release-please | Release-PR model: every release needs a second merge, which stalls Renovate's nightly dependency releases unless the release PR is automerged with an App/PAT token. Components are scoped to their own path, so "chart also counts image paths" needs workarounds. Writes versions into files. |
| semantic-release | Releases on merge, but multiple families only through community monorepo plugins built around `package.json` packages in subdirectories; the image here lives at the repo root. |
| Cocogitto | Could model the families (`include` / `ignore` globs per package), but still needs the same glue for image build, digest stamping, chart packaging and GitHub Releases, plus switching off its global tag, bump commits and changelog files. Tag format and 0.x behaviour unverified. |
| svu | Version calculator only, with fixed rules (`chore` never releases; the only knob releases a patch for every commit). No release notes, so git-cliff would be needed anyway. |
| convco | Versions and changelogs with git pathspecs, but per-type bump rules (`chore(deps)` → patch) and per-family prefixes are undocumented; smaller project. |
| One shared version for image and chart | Simpler, but Philipp wants image and chart releases distinguishable. |
| Develop inside agent-workstation, extract later | calcium already needs the same thing, so the component starts in its own repo. |
