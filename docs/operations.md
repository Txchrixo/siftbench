# Campaign operations

## 1. Install and verify the release

The package job builds a wheel and its SHA-256 checksum. Verify the wheel against the checksum from the trusted build, then install and run `reviewbench --help`. A fixture campaign and its report work without GitHub credentials. Use a fresh state directory for the smoke test.

```sh
python -m pip install ./siftbench-0.2.0-py3-none-any.whl
reviewbench --state-dir .siftbench/smoke run --manifest benchmark/fixtures/pagination/campaign.toml --fixture benchmark/fixtures/pagination/reviews.json
reviewbench --state-dir .siftbench/smoke report --run-id pagination-pilot --out dist/smoke
```

## 2. Prepare an approved corpus

The [v2 candidate catalog](../benchmark/evaluation-v2/candidates.json) is a seed list. For a new 45-PR campaign, put local source clones and exported PR metadata in a private `sources.json`. Each item in its `repositories` array has a GitHub `repository` slug, a `path` relative to the file and a `pulls` array. The minimal PR fields are `number`, `title`, `base_sha` (PR merge base) and `head_sha`. For merged PRs, supply `merge_sha` and optionally `search_tip` for the bounded follow-up search. Supply `fix_sha` if the linked correction is already known. Use `"kind": "clean"` to nominate a verified control candidate or `"kind": "context"` to nominate a case that requires project context. Validate the source metadata before using it.

Discovery runs offline, checks the license at both revisions and records why each candidate was selected or rejected. Its defaults target 45 PRs, five languages, at most four changes per project and roughly 27% clean controls. Review deficits in `candidates-report.md` and expand the source pool before approving the campaign. Public PRs may have been seen in model training or through external indexing.

```sh
reviewbench discover-cases --sources private/sources.json --out private/discovery
reviewbench draft-rubric --sources private/sources.json --catalog private/discovery/catalog.json --out private/rubric.json
```

The draft includes correction messages, diffs and test file names where available. Keep it private. Validate the evidence and enter `approved_by` and a time-zone-qualified `approved_at` for each case. Add an `expected` item with `expected_id`, `case_id`, `condition`, and `matching_criteria` for each reference problem. Add `severity` when the evidence supports it. Clean controls require approval and zero expected findings. Use human approval by default. If the operator explicitly delegates corpus approval to an AI reviewer, record the actual reviewer in `approved_by`, use `approval_kind: "operator_delegated_ai"`, and disclose the method in the campaign report. Set `neutral_title` on any case where the original title gives away the answer. Approve all material before viewing reviewer output.

Supply a private JSON array of [manifest targets](../benchmark/pilot/pagination/campaign.toml). Map CodeRabbit, Greptile and Proofline to separate private repositories for each project and condition. Record actual publisher, trigger, review-completion policy and known vendor version. Repository names come from the operator.

```sh
reviewbench prepare-repos --sources private/sources.json --catalog private/discovery/catalog.json --rubric private/rubric.json --targets private/targets.json --run-id cohort-2026-a --repeats 3 --out private/cohort-a
reviewbench plan --manifest private/cohort-a/campaign.json --limits private/limits.json
```

`prepare-repos` writes local bare repositories, the campaign manifest and its corpus lock. The original and replay SHAs are recorded separately. Each repetition has a distinct GitHub check identity and the same diff. Cross-case leakage checks reject known correction ancestry or an exact reverse-applicable correction in another snapshot. Review the snapshots for semantic clues and repository-level reviewer instructions as part of approval. A repeated review can still benefit from provider cache or learning, even in a fresh campaign.

The optional limits file maps each reviewer ID to `reviews_available`, `credits_available`, `credits_per_review` and, when known, `cost_minor_per_review` with an ISO currency. Omit unknown values. The GitHub request estimate splits preflight, PR creation and one poll of every open PR. Pagination, subsequent polling and retries add requests. Actual provider billing and wait times can vary.

## 3. Preflight, run, and resume

Get explicit operator approval for repository creation and seeding, app installation or configuration, the pilot, and the full launch. Before seeding the approved private repositories, disable GitHub Actions and provision only the prepared refs. Never push the original clones or private rubric. Create the empty targets and seed the local `main`, `siftbench-base/*` and `siftbench-input/*` refs after approval. When configuring reviewers, allow the `siftbench-base/*` branches, verify indexing scope, disable automatic changes and record settings that can affect repetition. Verify the pinned revisions with the read-only preflight.

```sh
reviewbench doctor --manifest private/cohort-a/campaign.json --github
reviewbench run --manifest private/cohort-a/campaign.json --execute --attempts private/pilot-pairs.json --max-attempts 10
reviewbench status --run-id cohort-2026-a --events
reviewbench resume --run-id cohort-2026-a --execute --attempts private/pilot-pairs.json --max-attempts 10
```

The pilot file lists nine distinct pair IDs from `plan` for the first passage on three cases across the three reviewers, plus one second-passage ID for a reviewer using a commit check. Include a clean control and a reference case. Verify clean completion, finding capture, cross-passage check identity and resume. After separate approval for the full launch, run `reviewbench resume --run-id cohort-2026-a --execute --max-attempts 405` using the same state directory. Three passages on 45 PRs across three reviewers give 405 planned attempts. Keep reviewer feedback and retries that modify inputs outside the scored campaign.

`--max-attempts` caps the selected or full plan. `--max-wait-seconds` caps local polling time. The review timeout starts at the verified PR opening or the attached manual trigger. An observation captured before the deadline is retained during stabilization.

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
reviewbench report --run-id cohort-2026-a --sources private/sources.json --out dist/cohort-a
reviewbench audit --manifest private/cohort-a/campaign.json --bundle dist/cohort-a/results.json --out private/audit.json
reviewbench compare-runs --before dist/cohort-a/results.json --after dist/cohort-b/results.json --out dist/compare
```

Record the actual roles of the independent annotators, the AI assistance and the operator in a plain-text note and pass it with `report --methodology`. Run `grade agree` on the independently annotated overlap and pass its output with `report --agreement` when available. An operator may validate an AI-assisted verdict directly with `grade import` when independent assessments were unavailable. An empty JSONL verdict file can load a pinned rubric for a campaign with no claims.

The primary score for the multi-project protocol uses matching passages completed and fully graded for every reviewer. The 95% intervals resample whole PRs with all their passages, using paired draws across reviewers. The overview shows the number of scored PRs and passages. Coverage shows attempts outside the scores, and Operational metrics counts timeouts, failures and outstanding grading. Use `export --visibility public` for aggregate results and `export --visibility private` for internal evidence. Review the HTML and its Ask content before sharing a private report.

Audit reads GitHub without changing the campaign. Exit code `2` means at least one source could not be verified. Review the details before treating an absence as zero findings. Run comparisons require matching pinned case inputs and rubric bytes. Use the paired sample rows to inspect cases and repetitions that completed in both runs, and use the separate coverage counts to see where the set changed. An unscored sample contributes no score.

To add a provider without rerunning the archived reviewers, generate its result bundle separately, then run `reviewbench extend-report` with both bundles and their pinned manifests. It requires disjoint reviewers, identical inputs and the same loaded rubric. The expanded report lists both source runs and scores only completed shared case conditions. Keep the source bundles for individual GitHub audits.

The five CodeRabbit verdicts were generated with AI assistance, then reviewed and approved by the operator, and checked against the pinned revisions using SQLite, CSV parsing, or the receiver simulation. The snapshot verdict records which parts of the Cedar criterion the finding covered. [CodeRabbit verdicts and test evidence](../benchmark/official/coderabbit-verdicts.jsonl)

To replay the archived grading, import the verdicts for the original claim IDs.

```bash
reviewbench grade import --run-id proofline-official-v1-coderabbit --rubric benchmark/official/expected.json --in benchmark/official/coderabbit-verdicts.jsonl
```

Operational metrics display the number of observations along with p50 and p95 timing. First useful finding uses the original publication time of the first finding confirmed by the rubric. False positives per PR use fully graded samples. Severe misses require a severity-tagged rubric. Cost is optional. Supply `report --costs` with a JSON array of `attempt_id`, `currency`, `amount_minor`, and `source` records based on observed billing. Keep unmeasured values absent. View a small sample's actual counts alongside its percentages and percentiles.

## 5. Share the appropriate artifact

```sh
reviewbench export --bundle dist/cohort-a/results.json --visibility private --out private/replay.json
reviewbench export --bundle dist/cohort-a/results.json --visibility public --out dist/public-summary.json
```

The private bundle can recreate the report and contains original reviewer text, diffs, and evidence. The allowlisted public JSON contains aggregate metrics and per-file anonymous reviewer labels. Use `--named-reviewers` only for a report approved for named publication. Treat the regular HTML, paired comparison, and audit as private artifacts. Confirm organizational approval to distribute the aggregate metrics before publishing them.

Public summaries and comparison JSON include `metric_availability` alongside numeric fields. For false positives, read this field and the number of graded PRs before interpreting a zero. A partially graded result covers only the graded subset. Timing percentiles remain `null` at zero observations. A replay bundle preserves source data and optional fields so the renderer can distinguish missing evidence from an empty result. Audit output distinguishes a missing PR, no archived review sources, a verification failure, and a verified source.
