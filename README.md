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

## Run an authorized GitHub pilot

Prepare one private test repository per provider and condition. Seed each repository with identical case commits. Its base branch must point at the pinned base SHA and the pinned head commit must be present before the runner creates an isolated attempt branch. Install each reviewer only on its designated repository. Never copy rubric or evaluation files into a test repository.

Authenticate locally with `gh auth login --hostname github.com`, or provide `SIFTBENCH_GITHUB_TOKEN` in the local environment. An existing `GH_TOKEN` is also accepted. Branch and PR creation need write access to Contents and Pull requests. Enable Checks read access if using a verified completion check. Scope the token to the pilot repositories when possible.

```sh
reviewbench doctor --manifest benchmark/pilot.toml --github
reviewbench plan --manifest benchmark/pilot.toml
reviewbench run --manifest benchmark/pilot.toml --execute --once
reviewbench status --run-id pilot
reviewbench resume --run-id pilot --execute
```

`--once` does one pass and leaves pending attempts resumable. `--poll-seconds` controls the waiting interval. An ambiguous create is never blindly repeated. Use `reviewbench reconcile --run-id pilot --attempt-id <id>` to attach an exact PR when it becomes visible. For a manual trigger, perform the provider's trigger in GitHub and use `reviewbench attach-trigger --run-id pilot --attempt-id <id> --comment-id <id>` to attach the specific operator-confirmed comment before resuming. Validate each provider's publisher and completion signal on a real pilot before scoring official cases.

The default CI workflow uses fixtures and read-only workflow permissions. The GitHub preflight is opt-in and checks privacy and pinned commits without creating branches, opening PRs, or requesting reviews.
