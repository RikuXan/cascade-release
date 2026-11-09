# Fixture consumer

A minimal image and chart that stand in for a consumer repository.

- `ci.yml` calls the reusable workflow against this directory with `dry-run: true`. The fixture's
  tag prefixes (`fixture-`, `fixture-chart-`) never get tags, so every dry run plans a first
  release and exercises the build and the packaging.
- `charts/fixture` implements the [chart contract](../docs/spec.md#chart-contract).
  `tests/test_chart_contract.py` renders it in every override combination.
