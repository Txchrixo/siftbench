import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

from .fixture import FixtureTransport
from .github import GitHubClient, RemoteError, verify_pull
from .grading import VerdictImporter, build_bundle, claims_for_grading
from .manifest import ManifestLoader, canonical_json
from .report import ReportBuilder
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
    runner = CampaignRunner(store, _live_client(manifest), execution_mode="github")
    active = {"planned", "preparing", "awaiting_review"}
    while True:
        rows = runner.step(manifest)
        if args.once or not any(row["status"] in active for row in rows):
            return rows
        time.sleep(args.poll_seconds)


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
        store.confirm_trigger(args.attempt_id, time.time(), timestamp, url)
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
    status = commands.add_parser("status")
    status.add_argument("--run-id", required=True)
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
    report = commands.add_parser("report")
    report_source = report.add_mutually_exclusive_group(required=True)
    report_source.add_argument("--run-id")
    report_source.add_argument("--bundle", type=Path)
    report.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    store = RunStore(args.state_dir)
    try:
        if args.command == "doctor":
            manifest = ManifestLoader.load(args.manifest)
            if args.github:
                client = _live_client(manifest)
                checked = set()
                for pair in manifest.pairs():
                    key = pair.target.repository, pair.variant.input_digest
                    if key not in checked:
                        client.check_input(pair)
                        checked.add(key)
            _print_json({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                         "manifest_digest": manifest.manifest_digest, "pairs": len(manifest.pairs()),
                         "github_checked": args.github})
        elif args.command == "plan":
            manifest = ManifestLoader.load(args.manifest)
            _print_json({"run_id": manifest.run_id, "manifest_digest": manifest.manifest_digest, "pairs": [
                {"pair_id": pair.pair_id, "case_id": pair.variant.case_id, "condition": pair.variant.condition,
                 "base_sha": pair.variant.base_sha, "head_sha": pair.variant.head_sha,
                 "repository": pair.target.repository, "provider_id": pair.target.provider_id,
                 "trigger": pair.target.trigger, "repeat_index": pair.repeat_index}
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
            _print_json(store.statuses(args.run_id))
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
            else:
                VerdictImporter(store).load(args.run_id, args.rubric, args.input)
        elif args.command == "report":
            builder = ReportBuilder()
            bundle = builder.load_bundle(args.bundle) if args.bundle else build_bundle(store, args.run_id)
            builder.write(bundle, args.out)
        else:
            parser.print_help()
    except (ValueError, OSError, sqlite3.Error, RemoteError) as error:
        print(f"reviewbench: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("reviewbench: interrupted; use status or resume to continue", file=sys.stderr)
        return 130
    return 0
