<p align="center">
  <img src="assets/brand/logo-readme.svg" alt="SiftBench" width="280">
</p>

<h1 align="center">Benchmark AI code reviewers</h1>

<p align="center">
  Run the same pinned changes through multiple reviewers and trace each score to the original comment and verification evidence.
</p>

<p align="center">
  <a href="https://github.com/Txchrixo/siftbench/actions/workflows/ci.yml"><img src="https://img.shields.io/badge/CI-Linux%20%26%20Windows-22C55E?labelColor=18181B" alt="CI runs on Linux and Windows"></a>
  <img src="https://img.shields.io/badge/Python-3.11%2B-38BDF8?labelColor=18181B" alt="Requires Python 3.11 or later">
  <img src="https://img.shields.io/badge/release-v0.1.0-2563EB?labelColor=18181B" alt="Qualified release v0.1.0">
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#official-evaluation">Results</a> ·
  <a href="#how-it-works">How it works</a> ·
  <a href="#run-a-github-pilot">GitHub pilot</a> ·
  <a href="ARCHITECTURE.md">Architecture</a>
</p>

---

SiftBench pairs a Python runner with an interactive HTML report. Configure a campaign, resume interrupted collection, grade findings against a frozen rubric, and compare reviewers on cases both completed. Live campaigns use GitHub pull requests. Local fixtures and report replay work offline.

## Quickstart

From the repository root, use Python 3.11 or later in a virtual environment. The included pagination fixture runs locally without credentials or provider calls.

```sh
python -m pip install -e .
reviewbench run --manifest benchmark/fixtures/pagination/campaign.toml --fixture benchmark/fixtures/pagination/reviews.json
reviewbench report --run-id pagination-pilot --out dist/pilot
```

Open `dist/pilot/report.html`. The first report shows captured fixture reviews and their grading status. To regenerate it from the saved results bundle, run `reviewbench report --bundle dist/pilot/results.json --out dist/replay`.

## Official evaluation

Proofline and Greptile reviewed eight pinned changes. Both also reviewed case 05 with a simulated receiver inventory, for 18 planned reviewer and case combinations.

| Analysis | Verified completions | Shared cases and conditions | Greptile reference recall | Proofline reference recall |
| --- | ---: | ---: | ---: | ---: |
| Frozen capture protocol | 11 formal reviews out of 18 attempts | 4 | 3/3 | 3/3 |
| Retrospective publication audit | 18 publications out of 18 attempts | 9 | 4/4 | 3/4 |

The audit verified seven further completions through GitHub checks and summaries. The shared subset changed between analyses. Case 05 follows one Greptile finding from an inline comment to a local check of the request size and a verdict against a **simulated** receiver limit. The snapshot comparison covers one case, and the separate live AWS invocation is pending. [Frozen protocol](benchmark/official/protocol.md) · [Case study](docs/case-study.md)

## How it works

1. **Pin the inputs.** Specify the commits, reviewer, condition, and completion signal in a campaign manifest.
2. **Run and resume.** Use dedicated GitHub repositories, then collect source comments against the expected revision. Resume using the same run ID.
3. **Verify and grade.** Match claims to reference findings or attach evidence for additional findings. Keep the source comment and rationale with the verdict.
4. **Compare and replay.** Read coverage and verdicts in the HTML report. Regenerate HTML, JSON, and Markdown from the archived bundle without calling reviewers again.

To include **Files changed**, pass `--source-repo` to `reviewbench report` with a local Git checkout containing the pinned base and head commits. The exported JSON carries the patches, so replay does not require that checkout. `--publication-audit` attaches a verified capture audit and its original GitHub sources without changing the scoring population. `--finding-groups` accepts evidence-backed links between additional findings from different reviewers, using the format in [the official comparison](benchmark/official/finding-groups.json).

<p align="center">
  <img src="diagrams/08-score-and-evidence-provenance.png" alt="SiftBench provenance from the GitHub review and pinned commit through verification evidence and scoring to the report" width="560">
</p>

For system boundaries, recovery flows, and the data model, see the [architecture](ARCHITECTURE.md) and [implementation plan](PLAN.md).

## Grade the local pilot

```sh
reviewbench plan --manifest benchmark/fixtures/pagination/campaign.toml
reviewbench grade export --run-id pagination-pilot --out .siftbench/claims.jsonl
```

Use the exported `claim_id` to write one JSON object per line in a verdict file. A confirmed reference finding requires its `expected_id` and at least one piece of evidence.

```json
{"claim_id":"<from claims.jsonl>","decision":"confirmed_expected","expected_id":"same-time","rationale":"The timestamp-only cursor skips ties.","evidence":[{"type":"test","text":"A second row with the same timestamp is missing from page two."}]}
```

Import the verdict with `reviewbench grade import --run-id pagination-pilot --rubric benchmark/fixtures/pagination/expected.json --in .siftbench/verdicts.jsonl`, then generate the report again. Fixture results stay labeled in the report.

## Run a GitHub pilot

Prepare one private test repository per provider and condition. Seed each repository with identical case commits. Its base branch must point at the pinned base SHA and the pinned head commit must be present before the runner creates an isolated attempt branch. Install each reviewer only on its designated repository. Never copy rubric or evaluation files into a test repository.

The independent pagination pilot uses [its own manifest](benchmark/pilot/pagination/campaign.toml). Only `README.md` and the appropriate revision of `reports.py` go into a reviewer repository. Keep the expected findings and `tests/test_live_pilot_case.py` in SiftBench. The current manifest uses `pagination-live-pilot-v2` with the verified Proofline publisher. The original `pagination-live-pilot` run retains its original configuration and evidence.

Authenticate locally with `gh auth login --hostname github.com`. SiftBench uses that session through `gh api`. In CI or other environments with an explicit token, set `SIFTBENCH_GITHUB_TOKEN` or `GH_TOKEN` to use the built-in HTTPS transport. Branch and PR creation need write access to Contents and Pull requests. Enable Checks read access if using a verified completion check. Scope the token to the pilot repositories when possible.

```sh
reviewbench doctor --manifest benchmark/pilot/pagination/campaign.toml --github
reviewbench plan --manifest benchmark/pilot/pagination/campaign.toml
reviewbench run --manifest benchmark/pilot/pagination/campaign.toml --execute --once
reviewbench status --run-id pagination-live-pilot-v2
reviewbench resume --run-id pagination-live-pilot-v2 --execute
```

`--once` does one pass and leaves pending attempts resumable. `--poll-seconds` controls the waiting interval. An ambiguous create is never blindly repeated. Use `reviewbench reconcile --run-id <run-id> --attempt-id <id>` to attach an exact PR when it becomes visible. For a manual trigger, perform the provider's trigger in GitHub and use `reviewbench attach-trigger --run-id <run-id> --attempt-id <id> --comment-id <id>` to attach the specific operator-confirmed comment before resuming. Validate each provider's publisher and completion signal on a real pilot before scoring official cases.

The default CI workflow uses fixtures and read-only workflow permissions. The optional `doctor --github` command checks privacy and pinned commits without creating branches, opening PRs, or requesting reviews.

Keep the run store and raw artifacts private. The HTML and JSON exports still include original reviewer text and evidence excerpts. Read the bundle and check who can open its source links before sharing it. Once the pilot is archived, close the experimental PRs without merging and remove reviewer access when those repositories are no longer needed. Retain the original PRs when a report links to them.

## Add a reviewer

1. Prepare a private repository for the provider and condition. Seed the same application refs as the comparison repository and scope the new GitHub App to its own test repository.
2. Add that repository to the manifest allowlist and add a `[[targets]]` entry with its provider ID, condition, repository, verified GitHub publisher, and trigger mode. Use a new run ID when the configuration changes.
3. Use an independent case to check where the app publishes findings and how it signals a finished review with no findings. `ReviewerAdapter` accepts a submitted GitHub review by the configured publisher on the pinned head. If the app relies on a GitHub check, set `completion_check` only after verifying the exact check name, app slug, pull request association, and outcomes. A provider that publishes through a different API needs a corresponding transport and contract tests before it can count toward coverage.
4. Run the manifest preflight and the pilot. Resume it on the existing PR and verify the exported source links. See `tests/test_github.py` for the publisher, revision, private repository, and rate-limit contract.

## Development

```sh
python -m pip install -e ".[test]"
python -m pytest
python -m compileall -q src
reviewbench --help
```

The default tests use local fixtures and temporary data. They avoid GitHub reviewer calls and AWS deployments. For manual CLI checks from a source checkout, add `src` to `PYTHONPATH` and use `python -m reviewbench`.

## Access and evidence

Develop and test against independent fixture cases before scheduling an official campaign. Store tokens outside the repository and scope reviewer installations to their assigned repositories. Deployment verification uses a separate AWS role and starts only when requested by the operator. [Access checklist](PLAN.md#10-confirm-before-remote-operations)

The runner verifies artifacts before storing their SQLite references. An interruption can leave an unreferenced file outside the report. If a referenced artifact is missing or its digest differs, restore the original bytes from the archive and verify the digest before reporting that evidence.
