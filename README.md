# cascade-release

Reusable GitHub Actions workflow that releases a container image and the Helm chart that ships it
as two independent version families, derived from conventional commits. Every image release
cascades into a chart release that pins the new image by digest; chart-only changes release only
the chart.

**Status:** specification only. Nothing is implemented yet.

| Document | Content |
| --- | --- |
| [docs/requirements.md](docs/requirements.md) | What the component has to do and why, non-goals, decisions and rejected alternatives |
| [docs/spec.md](docs/spec.md) | Intended design: interface, versioning rules, planning algorithm, jobs, chart contract, testing, pitfalls |
| [docs/consumers.md](docs/consumers.md) | The first consumers (agent-workstation, calcium) and how each migrates |
