import argparse
import json
import os
import sqlite3
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .analysis import attach_costs, campaign_metrics, compare_campaigns, freeze_corpus
from .audit import audit_campaign
from .corpus import prepare_corpus
from .fixture import FixtureTransport
from .github import GitHubClient, RemoteError, verify_pull
from .grading import VerdictImporter, assessments_for_run, build_bundle, claims_for_grading
from .manifest import ManifestLoader, canonical_json, digest
from .report import ReportBuilder
from .report.context import attach_changes, attach_conversations, attach_publications
from .report.comparison import write_comparison
from .runner import CampaignRunner
from .store import RunStore


def _print_json(value: object) -> None:
    print(canonical_json(value).decode())


def _live_client(manifest) -> GitHubClient:
    token = os.environ.get("SIFTBENCH_GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    return GitHubClient(token, manifest.allowed_repositories)


def _run_live(store: RunStore, manifest, args) -> list[dict]:
    if not args.execute:
        raise ValueError("Live GitHub execution requires --execute")
    if not 1 <= args.poll_seconds <= 60:
        raise ValueError("--poll-seconds must be between 1 and 60")
    if args.max_wait_seconds < 1:
        raise ValueError("--max-wait-seconds must be positive")
    if args.max_attempts is not None and (args.max_attempts < 1 or len(manifest.pairs()) > args.max_attempts):
        raise ValueError("The planned attempt count exceeds --max-attempts, or the limit is invalid")
    if args.max_active_per_reviewer < 1:
        raise ValueError("--max-active-per-reviewer must be positive")
    deadline = time.monotonic() + args.max_wait_seconds
    runner = CampaignRunner(store, _live_client(manifest), execution_mode="github", max_active_per_reviewer=args.max_active_per_reviewer)
    active = {"planned", "preparing", "awaiting_review"}
    while True:
        rows = runner.step(manifest)
        remaining = deadline - time.monotonic()
        if args.once or remaining <= 0 or not any(row["status"] in active for row in rows):
            return rows
        time.sleep(min(args.poll_seconds, remaining))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value) + b"\n")


def _runner_version() -> str:
    try:
        return version("siftbench")
    except PackageNotFoundError:
        return "source checkout"


def _attach_trigger(store: RunStore, args) -> str:
    manifest = store.load_manifest(args.run_id)
    store.require_live(manifest.run_id)
    with store.lock(manifest.run_id):
        attempt = store.get_attempt(args.attempt_id)
        pair = next((pair for pair in manifest.pairs() if pair.pair_id == attempt["pair_id"]), None)
        if pair is None or attempt["status"] != "awaiting_trigger" or args.comment_id < 1:
            raise ValueError("Expected a waiting attempt and a positive comment ID")
        client = _live_client(manifest)
        pr = client.get_pull(pair.target.repository, attempt["pr_number"])
        verify_pull(pair, pr, f"siftbench/{attempt['attempt_id']}")
        url, timestamp = client.verify_trigger_comment(pr, args.comment_id, attempt["started_at"])
        store.confirm_trigger(args.attempt_id, time.time(), timestamp, url, manifest.timeout_seconds)
        return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reviewbench", description="Run and compare AI code reviewers."
    )
    parser.add_argument("--state-dir", type=Path, default=Path(".siftbench"))
    commands = parser.add_subparsers(dest="command")
    for name in ("doctor", "plan"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
        if name == "doctor":
            command.add_argument("--github", action="store_true", help="Check authorized private repositories and pinned commits")
            command.add_argument("--pilot-bundle", type=Path, help="Verify prior provider publications with their pinned manifest and GitHub")
    run = commands.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--run-id")
    run_mode = run.add_mutually_exclusive_group()
    run_mode.add_argument("--fixture", type=Path)
    run_mode.add_argument("--execute", action="store_true")
    resume = commands.add_parser("resume")
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--execute", action="store_true")
    for live_command in (run, resume):
        live_command.add_argument("--once", action="store_true")
        live_command.add_argument("--poll-seconds", type=int, default=5)
        live_command.add_argument("--max-wait-seconds", type=int, default=3600, help="Return resumable state after this polling budget")
        live_command.add_argument("--max-active-per-reviewer", type=int, default=4, help="Limit simultaneous attempts for each reviewer")
        live_command.add_argument("--max-attempts", type=int, help="Refuse the campaign if its planned total exceeds this count")
    status = commands.add_parser("status")
    status.add_argument("--run-id", required=True)
    status.add_argument("--events", action="store_true", help="Include the ordered attempt event log in the private output")
    trigger = commands.add_parser("attach-trigger")
    trigger.add_argument("--run-id", required=True)
    trigger.add_argument("--attempt-id", required=True)
    trigger.add_argument("--comment-id", type=int, required=True)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--run-id", required=True)
    reconcile.add_argument("--attempt-id", required=True)
    grade = commands.add_parser("grade").add_subparsers(dest="grade_action", required=True)
    grade_export = grade.add_parser("export")
    grade_export.add_argument("--run-id", required=True)
    grade_export.add_argument("--out", type=Path, required=True)
    grade_export.add_argument("--unblind", action="store_true")
    grade_import = grade.add_parser("import")
    grade_import.add_argument("--run-id", required=True)
    grade_import.add_argument("--rubric", type=Path, required=True)
    grade_import.add_argument("--in", dest="input", type=Path, required=True)
    grade_role = grade_import.add_mutually_exclusive_group()
    grade_role.add_argument("--grader-id", help="Record a private independent assessment without updating scores")
    grade_role.add_argument("--adjudicator-id", help="Record a final verdict after two independent assessments")
    grade_assessments = grade.add_parser("assessments", help="Export private assessment history for adjudication")
    grade_assessments.add_argument("--run-id", required=True)
    report = commands.add_parser("report")
    report_source = report.add_mutually_exclusive_group(required=True)
    report_source.add_argument("--run-id")
    report_source.add_argument("--bundle", type=Path)
    report.add_argument("--out", type=Path, required=True)
    report.add_argument("--source-repo", type=Path, help="Read full diffs from pinned commits in a local Git repository")
    report.add_argument("--publication-audit", type=Path, help="Attach verified publication evidence without changing scores")
    report.add_argument("--github-reconciliation", type=Path, help="Link additional GitHub conversation comments without changing scores")
    report.add_argument("--finding-groups", type=Path, help="Attach manually verified groups of additional findings")
    report.add_argument("--costs", type=Path, help="Attach observed cost evidence keyed by attempt ID")
    audit = commands.add_parser("audit", help="Reconcile a frozen bundle with GitHub without changing remote or local run state")
    audit.add_argument("--manifest", type=Path, required=True)
    audit.add_argument("--bundle", type=Path, required=True)
    audit.add_argument("--out", type=Path, required=True)
    export = commands.add_parser("export", help="Export aggregate public data or the complete private result bundle")
    export.add_argument("--bundle", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--visibility", choices=("public", "private"), required=True)
    export.add_argument("--named-reviewers", action="store_true", help="Retain reviewer identifiers in a public export")
    comparison = commands.add_parser("compare-runs", help="Compare two campaigns using paired samples and fixed pinned inputs")
    comparison.add_argument("--before", type=Path, required=True)
    comparison.add_argument("--after", type=Path, required=True)
    comparison.add_argument("--out", type=Path, required=True)
    corpus = commands.add_parser("freeze-corpus", help="Record input fingerprints and rubric for a repeatable test corpus")
    corpus.add_argument("--manifest", type=Path, required=True)
    corpus.add_argument("--rubric", type=Path, required=True)
    corpus.add_argument("--out", type=Path, required=True)
    prepare = commands.add_parser("prepare-corpus", help="Build a local manifest from a catalog, approved rubric and private targets")
    prepare.add_argument("--catalog", type=Path, required=True)
    prepare.add_argument("--rubric", type=Path, required=True)
    prepare.add_argument("--targets", type=Path, required=True)
    prepare.add_argument("--run-id", required=True)
    prepare.add_argument("--repeats", type=int, default=1)
    prepare.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    store = RunStore(args.state_dir)
    try:
        if args.command == "doctor":
            manifest = ManifestLoader.load(args.manifest)
            if args.pilot_bundle and not args.github:
                raise ValueError("--pilot-bundle requires --github")
            witnessed = set()
            if args.github:
                client = _live_client(manifest)
                checked = set()
                for pair in manifest.pairs():
                    key = pair.target.repository, pair.variant.input_digest
                    if key not in checked:
                        client.check_input(pair)
                        checked.add(key)
                if args.pilot_bundle:
                    pilot = ReportBuilder.load_bundle(args.pilot_bundle)
                    audited = audit_campaign(pilot, manifest, client)
                    witnessed = {(row["provider"], row["condition"], row.get("isolation_group")) for row in audited
                                 if row["publication_sources"] and not row["differences"]}
            _print_json({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                         "manifest_digest": manifest.manifest_digest, "pairs": len(manifest.pairs()),
                         "github_checked": args.github,
                         "providers": [{"provider": target.provider_id, "condition": target.condition, "isolation_group": target.isolation_group,
                                        "publication_evidence": "verified" if (target.provider_id, target.condition, target.isolation_group) in witnessed else "unverified"}
                                       for target in manifest.targets]})
        elif args.command == "plan":
            manifest = ManifestLoader.load(args.manifest)
            _print_json({"run_id": manifest.run_id, "manifest_digest": manifest.manifest_digest, "pairs": [
                {"pair_id": pair.pair_id, "case_id": pair.variant.case_id, "condition": pair.variant.condition,
                 "base_sha": pair.variant.base_sha, "head_sha": pair.variant.head_sha,
                 "repository": pair.target.repository, "provider_id": pair.target.provider_id,
                 "trigger": pair.target.trigger, "repeat_index": pair.repeat_index, "isolation_group": pair.target.isolation_group}
                for pair in manifest.pairs()
            ]})
        elif args.command == "run":
            manifest = ManifestLoader.load(args.manifest)
            if args.run_id is not None and args.run_id != manifest.run_id:
                raise ValueError("--run-id does not match the pinned manifest")
            if args.fixture:
                if manifest.settling_seconds:
                    raise ValueError("Local fixtures require settling_seconds=0")
                _print_json(CampaignRunner(store, FixtureTransport.load(args.fixture)).step(manifest))
            else:
                _print_json(_run_live(store, manifest, args))
        elif args.command == "resume":
            _print_json(_run_live(store, store.load_manifest(args.run_id), args))
        elif args.command == "status":
            store.load_manifest(args.run_id)
            attempts = store.statuses(args.run_id)
            _print_json({"attempts": attempts, "events": store.events(args.run_id)} if args.events else attempts)
        elif args.command == "attach-trigger":
            _print_json({"trigger_url": _attach_trigger(store, args)})
        elif args.command == "reconcile":
            manifest = store.load_manifest(args.run_id)
            store.require_live(manifest.run_id)
            runner = CampaignRunner(store, _live_client(manifest), execution_mode="github")
            _print_json({"pr_url": runner.reconcile(manifest, args.attempt_id)})
        elif args.command == "grade":
            if args.grade_action == "export":
                rows = claims_for_grading(store, args.run_id, blind=not args.unblind)
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_bytes(b"".join(canonical_json(row) + b"\n" for row in rows))
            elif args.grade_action == "assessments":
                _print_json(assessments_for_run(store, args.run_id))
            else:
                VerdictImporter(store).load(args.run_id, args.rubric, args.input,
                                            grader_id=args.grader_id, adjudicator_id=args.adjudicator_id)
        elif args.command == "report":
            builder = ReportBuilder()
            bundle = builder.load_bundle(args.bundle) if args.bundle else build_bundle(store, args.run_id)
            if args.source_repo:
                attach_changes(bundle, args.source_repo)
            if args.publication_audit:
                attach_publications(bundle, json.loads(args.publication_audit.read_bytes()))
            if args.github_reconciliation:
                attach_conversations(bundle, json.loads(args.github_reconciliation.read_bytes()))
            if args.finding_groups:
                bundle["finding_groups"] = json.loads(args.finding_groups.read_bytes())
            if args.costs:
                attach_costs(bundle, json.loads(args.costs.read_bytes()))
            builder.write(bundle, args.out)
        elif args.command == "audit":
            manifest = ManifestLoader.load(args.manifest)
            result = audit_campaign(ReportBuilder.load_bundle(args.bundle), manifest, _live_client(manifest))
            _write_json(args.out, result)
            mismatched = sum(bool(item["differences"]) for item in result)
            _print_json({"pairs": len(result), "mismatched": mismatched})
            return 2 if mismatched else 0
        elif args.command == "export":
            bundle = ReportBuilder.load_bundle(args.bundle)
            if args.visibility == "public":
                output = campaign_metrics(bundle, reviewer_names=args.named_reviewers)
            else:
                output = dict(bundle)
                output["export_provenance"] = {"visibility": "private", "bundle_digest": digest(canonical_json(bundle)),
                                               "runner_version": _runner_version()}
            _write_json(args.out, output)
        elif args.command == "compare-runs":
            before = ReportBuilder.load_bundle(args.before)
            after = ReportBuilder.load_bundle(args.after)
            write_comparison(compare_campaigns(before, after), args.out)
        elif args.command == "freeze-corpus":
            frozen = freeze_corpus(ManifestLoader.load(args.manifest), args.rubric.read_bytes())
            _write_json(args.out, frozen)
        elif args.command == "prepare-corpus":
            if args.out.suffix.lower() != ".json":
                raise ValueError("Prepared corpus manifest must have a .json extension")
            manifest = prepare_corpus(json.loads(args.catalog.read_bytes()), json.loads(args.targets.read_bytes()),
                                      args.rubric.read_bytes(), args.run_id, args.repeats)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_bytes(manifest.json + b"\n")
        else:
            parser.print_help()
    except (ValueError, OSError, sqlite3.Error, RemoteError) as error:
        print(f"reviewbench: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("reviewbench: interrupted; use status or resume to continue", file=sys.stderr)
        return 130
    return 0
