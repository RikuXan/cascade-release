# Consumers

## agent-workstation

First consumer. Its Argo CD deployment on hk3s waits for the first release from
cascade-release.

### State on 2026-11-09

- `main` was last released as `chart-v2.1.0`. Its `release.yml` reads `major.minor` from
  `ARG IMAGE_VERSION` in the Dockerfile and from `Chart.yaml`, stamps the patch, and writes the
  image into the public `image.tag` / `image.digest` of `values.yaml`.
- Seven commits are unmerged, stacked on branch `feat/vscode-remote-ssh`:
  - SSH-agent signing
  - herdr, sshd and cron as s6 services in one container (breaking)
  - the Remote Control server as an s6 service (breaking)
  - the image user renamed to `philipp.holler` (breaking)
  - MCP servers through `managed-mcp.json`
  - OpenSCAD
  - VS Code Remote-SSH forwarding

  On that branch, `Chart.yaml` says `3.1.0`.
- **Released so far:**
  - git tags `v1.0.0`, `v1.1.0`, `chart-v1.0.0`, `chart-v1.0.1`, `chart-v2.0.0` and
    `chart-v2.1.0`
  - versions of the private packages `ghcr.io/rikuxan/agent-workstation` and
    `ghcr.io/rikuxan/charts/agent-workstation`
  - no GitHub Releases
- The pod on hk3s runs a hand-installed chart with the image tag `dev`, pinned by digest.
- Renovate is configured to release the pod's Claude Code only on its `stable` channel, and
  commits Dockerfile `ARG` bumps as `chore(deps)`, which release a patch under R4. Check the
  actual commit prefix on the first Renovate PR.

### Decisions

- Tags: image `X.Y.Z`, chart `chart-X.Y.Z`, both starting at `0.1.0`. The old versions are
  deleted rather than continued.
- The stack and the migration reach `main` by a direct push. From the Argo CD cutover on, changes
  go through GitHub pull request → merge → release → Renovate / Argo CD.

### Migration

1. **In agent-workstation, on top of the stack:**
   - drop `ARG IMAGE_VERSION` from the Dockerfile
   - set `Chart.yaml` to `version: 0.0.0` / `appVersion: "0.0.0"` and drop the comment about
     `release.yml`
   - implement the [chart contract](spec.md#chart-contract) in `_helpers.tpl` and `values.yaml`
     (`image.shipped`)
   - replace `release.yml` with a caller of cascade-release, with image paths `Dockerfile` and
     `rootfs/**` and chart path `charts/agent-workstation`
   - keep `ci.yml`
2. **Reset the old releases:**
   - delete the six git tags on `origin`
   - delete every version of both ghcr packages except the image tag `dev`, which the pod still
     runs. This needs `gh auth refresh -h github.com -s read:packages,delete:packages`.
   - keep the packages themselves, so their Actions access settings survive
3. **Push** the stack and the migration to `main`. The first run releases `0.1.0` and
   `chart-0.1.0`.
4. **In hk3s-argocd** (branch `feat/agent-workstation`): point the Application's chart from
   `3.1.0` to `0.1.0`. The existing Renovate rule lands chart updates between 00:00 and 04:00
   Europe/Berlin. The cutover itself happens there and is out of scope here:
   - hand ownership of the live objects from Helm and kubectl to Argo CD
   - delete the old Helm release secrets
   - merge
5. **After the cutover**, delete the image tag `dev`.

## calcium

Second consumer, migrated after agent-workstation works.

### State on 2026-11-09

- Releases start from hand-pushed tags:
  - `release-app.yml` runs on `v*`. It refuses an app tag when `charts/` changed without a
    companion `chart-v*` tag on the same commit. It builds `ghcr.io/rikuxan/calcium` with
    `build-args: VERSION=<version>`, after pulling Git LFS data the image ships.
  - `release-chart.yml` runs on `chart-v*` and calls the reusable `publish-chart.yml`. That
    workflow takes the newest `v*` tag as app version, waits up to 30 minutes for the image
    digest, and pushes to `oci://ghcr.io/rikuxan/charts`.
- `charts/calcium/Chart.yaml` already uses `0.0.0` placeholders.

### Needs from cascade-release

- A `build-args` input whose values can refer to the released image version, e.g. a
  `VERSION={version}` placeholder that the image job substitutes.
- An `lfs` input that checks out Git LFS content before the build.
- A tag scheme decision: keep `v*` / `chart-v*` through the prefix inputs and continue from the
  existing versions, or reset like agent-workstation.
- Read its workflows in full before migrating; the guard and the wait logic disappear, because
  one run publishes both.

## Not consumers

- **hk3s-images** builds several images and no chart; that's outside the one-image-one-chart scope
  (see the non-goals in [requirements.md](requirements.md#non-goals-for-now)).
