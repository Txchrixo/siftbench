# Campaign operations

## 1. Install and verify the release

The package job builds a wheel and its SHA-256 checksum. Verify the wheel against the checksum from the trusted build, then install and run `reviewbench --help`. A fixture campaign and its report work without GitHub credentials. Use a fresh state directory for the smoke test.

```sh
python -m pip install ./siftbench-0.2.0-py3-none-any.whl
reviewbench --state-dir .siftbench/smoke run --manifest benchmark/fixtures/pagination/campaign.toml --fixture benchmark/fixtures/pagination/reviews.json
reviewbench --state-dir .siftbench/smoke report --run-id pagination-pilot --out dist/smoke
```

## 2. Prepare an approved corpus

The [v2 candidate catalog](../benchmark/evaluation-v2/candidates.json) pins public code from multiple repositories and records the license and origin of each change. Public PR history may be known to reviewers through training or external indexing. Use these candidates to calibrate the harness and record that exposure when interpreting results. An independent, held-out set is needed to measure performance on unseen changes.

Keep the approvals and `expected` findings in a private rubric. In its `cases` array, use `case_id`, `condition`, and `kind`. Use `clean` for a control independently checked to have no reference findings, `reference` for a case with at least one verified finding, and `context` for a case that evaluates the use of additional information. In the `expected` array, provide `expected_id`, `case_id`, `condition`, and `matching_criteria` as in the [existing rubric](../benchmark/official/expected.json). Add `"severity": "high"` or `"severity": "critical"` when supported by the evidence. Approve controls and record the rubric before looking at provider output. The original suite includes a deployment-context example in case 05.

The private targets file contains a JSON array of [manifest targets](../benchmark/pilot/pagination/campaign.toml). Assign a dedicated repository per reviewer, condition, and `isolation_group`. For each group, seed the pinned Git objects and set `siftbench-base/<case_id>` to the pinned base before running. Keep evaluation files, review history from other providers, and unrelated upstream history outside the test surface. Check the scope of each provider installation in GitHub. During repeat campaigns, record the provider version and settings when available, and document potential carryover if the provider retains repository history across attempts. Dedicated fresh repositories and isolated provider settings are needed when estimating independent repeatability.

```sh
reviewbench prepare-corpus --catalog benchmark/evaluation-v2/candidates.json --rubric private/rubric.json --targets private/targets.json --run-id cohort-2026-a --repeats 2 --out private/campaign.json
reviewbench freeze-corpus --manifest private/campaign.json --rubric private/rubric.json --out private/corpus-lock.json
```

## 3. Preflight, run, and resume

Check the plan before opening PRs. The network preflight verifies the private repositories and pinned refs. Repeat it with `--pilot-bundle` and the corresponding pilot manifest to verify published evidence. A prior observed publication provides stronger evidence of an operational integration than seeing a repository in the app settings. Verify two examples per provider before using it in a scored campaign, one with an inline finding and one with a confirmed clean completion.

```sh
reviewbench plan --manifest private/campaign.json
reviewbench doctor --manifest private/campaign.json --github
reviewbench run --manifest private/campaign.json --execute --once --max-active-per-reviewer 2 --max-attempts 40
reviewbench status --run-id cohort-2026-a --events
reviewbench resume --run-id cohort-2026-a --execute --max-wait-seconds 900 --max-active-per-reviewer 2 --max-attempts 40
```

Use the same run ID and state directory after interruption. `--max-attempts` caps planned work, and `--max-wait-seconds` caps local polling time. Actual provider charges depend on the provider. The review timeout starts at the verified PR opening or the attached manual trigger. An observation captured before the deadline is retained during stabilization. `review_or_check` is an explicit policy for a provider whose authenticated, PR-associated check signals completion even when there are zero findings. Verify that behavior in a pilot before opting in.

For CodeRabbit, set **Configuration → Reviews → Summary → Placement** to **walkthrough** on each benchmark repository before opening a scored PR. In the dashboard's V3 YAML editor, use `reviews.output.summary.placement: walkthrough` as a nested setting. The independently versioned [pilot configuration](../benchmark/pilot/coderabbit/.coderabbit.yaml) uses V2, where the equivalent key is `reviews.high_level_summary_in_walkthrough: true`. The default appends release notes to the PR description and invalidates the pinned input. [CodeRabbit V3 reference](https://docs.coderabbit.ai/reference/configuration-v3)

Use `publisher = "coderabbitai[bot]"` and `completion_check = "CodeRabbit"` so a clean review can complete through its check. When CodeRabbit's GitHub check association list is empty, SiftBench verifies the exact CodeRabbit PR URL, app identity, head SHA, and explicit successful review outcome. Walkthroughs stay in the private capture as publication context. Record the scope of provider learnings and use separate fresh repositories for the scored campaign.

Use short-lived [GitHub App installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app) for automated operations. Mint a token for the specific private target repositories and pass it to `SIFTBENCH_GITHUB_TOKEN` as a job secret. Use Contents and Pull requests write for execution, and grant Checks read when required by the completion policy. Use a read-only token for audit. Keep the runner's GitHub App separate from the reviewer installations. Preserve the state directory and original artifacts with private access between jobs.

## 4. Grade, reconcile, and compare

Export one claim packet and give independent graders separate copies. The default export excludes explicit provider identifiers and source links. Vendor phrasing may still signal the source. Use distinct grader IDs, export the assessment history, and import a final verdict under a separate adjudicator ID after both reviewers have assessed that claim. Assessments are private and only final verdicts are scored.

```sh
reviewbench grade export --run-id cohort-2026-a --out private/claims.jsonl
reviewbench grade import --run-id cohort-2026-a --rubric private/rubric.json --grader-id reviewer-a --in private/verdicts-a.jsonl
reviewbench grade import --run-id cohort-2026-a --rubric private/rubric.json --grader-id reviewer-b --in private/verdicts-b.jsonl
reviewbench grade assessments --run-id cohort-2026-a
reviewbench grade import --run-id cohort-2026-a --rubric private/rubric.json --adjudicator-id referee --in private/final-verdicts.jsonl
reviewbench report --run-id cohort-2026-a --out dist/cohort-a
reviewbench audit --manifest private/campaign.json --bundle dist/cohort-a/results.json --out private/audit.json
reviewbench compare-runs --before dist/cohort-a/results.json --after dist/cohort-b/results.json --out dist/compare
```

Audit reads GitHub without changing the campaign. Exit code `2` means at least one source could not be verified. Review the details before treating an absence as zero findings. Run comparisons require matching pinned case inputs and rubric bytes. Use the paired sample rows to inspect cases and repetitions that completed in both runs, and use the separate coverage counts to see where the set changed. An unscored sample contributes no score.

To add a provider without rerunning the archived reviewers, generate its result bundle separately, then run `reviewbench extend-report` with both bundles and their pinned manifests. It requires disjoint reviewers, identical inputs and the same loaded rubric. The expanded report lists both source runs and scores only completed shared case conditions. Keep the source bundles for individual GitHub audits.

The [CodeRabbit official verdict file](../benchmark/official/coderabbit-verdicts.jsonl) records the AI evaluation authorized by the operator. Its five decisions were verified against the pinned application with SQLite, CSV parsing, or the specified receiver simulation. Import it with `reviewbench grade import --run-id proofline-official-v1-coderabbit --rubric benchmark/official/expected.json --in benchmark/official/coderabbit-verdicts.jsonl` when replaying the same archive. The verdicts apply to those original claim IDs. The snapshot verdict applies the same Cedar criterion as the archived evaluation and records the details omitted by the reviewer.

Operational metrics display the number of observations along with p50 and p95 timing. First useful finding uses the original publication time of the first finding confirmed by the rubric. False positives per PR use fully graded samples. Severe misses require a severity-tagged rubric. Cost is optional. Supply `report --costs` with a JSON array of `attempt_id`, `currency`, `amount_minor`, and `source` records based on observed billing. Keep unmeasured values absent. View a small sample's actual counts alongside its percentages and percentiles.

## 5. Share the appropriate artifact

```sh
reviewbench export --bundle dist/cohort-a/results.json --visibility private --out private/replay.json
reviewbench export --bundle dist/cohort-a/results.json --visibility public --out dist/public-summary.json
```

The private bundle can recreate the report and contains original reviewer text, diffs, and evidence. The allowlisted public JSON contains aggregate metrics and per-file anonymous reviewer labels. Use `--named-reviewers` only for a report approved for named publication. Treat the regular HTML, paired comparison, and audit as private artifacts. Confirm organizational approval to distribute the aggregate metrics before publishing them.

Public summaries and comparison JSON include `metric_availability` alongside numeric fields. For false positives, read this field and the number of graded PRs before interpreting a zero. A partially graded result covers only the graded subset. Timing percentiles remain `null` at zero observations. A replay bundle preserves source data and optional fields so the renderer can distinguish missing evidence from an empty result. Audit output distinguishes a missing PR, no archived review sources, a verification failure, and a verified source.
