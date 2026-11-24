# SiftBench

SiftBench compares AI code reviewers on pinned changes and traces each verdict to its evidence. See the [implementation plan](PLAN.md) and [architecture](ARCHITECTURE.md).

## Development

Use Python 3.11 or later. Install the package and test dependencies in a virtual environment.

```sh
python -m pip install -e ".[test]"
python -m pytest
python -m compileall -q src
reviewbench --help
```

The default tests use local fixtures and temporary data. They avoid GitHub reviewer calls and AWS deployments.

To run from an existing checkout with pytest installed, `python -m pytest` resolves `src` from `pyproject.toml`. For manual CLI checks from the checkout, add `src` to `PYTHONPATH` and use `python -m reviewbench`.

## Access boundaries

Develop and test against independent fixture cases before scheduling official cases. Store actual tokens outside the repository. Live integration needs access to the authorized test repositories and reviewer installations scoped to those repositories. A deployment verification run needs a separate AWS role and explicit operator initiation. See [the plan's access checklist](PLAN.md#10-confirm-before-remote-operations).

The runner writes and verifies artifacts before storing their SQLite references. An interruption can leave an unreferenced file. Leave it outside the report. If a referenced file is missing or its digest differs, recover the original bytes from the archive and check the digest before reporting that evidence.

## Run the local pilot

```sh
reviewbench plan --manifest benchmark/fixtures/pagination/campaign.toml
reviewbench run --manifest benchmark/fixtures/pagination/campaign.toml --fixture benchmark/fixtures/pagination/reviews.json
reviewbench grade export --run-id pagination-pilot --out .siftbench/claims.jsonl
reviewbench report --run-id pagination-pilot --out dist/pilot
reviewbench report --bundle dist/pilot/results.json --out dist/replay
```

Use the exported `claim_id` to write one JSON object per line in a verdict file. A confirmed reference finding requires its `expected_id` and at least one piece of evidence.

```json
{"claim_id":"<from claims.jsonl>","decision":"confirmed_expected","expected_id":"same-time","rationale":"The timestamp-only cursor skips ties.","evidence":[{"type":"test","text":"A second row with the same timestamp is missing from page two."}]}
```

Import the verdict with `reviewbench grade import --run-id pagination-pilot --rubric benchmark/fixtures/pagination/expected.json --in .siftbench/verdicts.jsonl`, then generate the report again. Fixture results stay labeled in the report.
