import argparse
import sqlite3
import sys
from pathlib import Path

from .fixture import FixtureTransport
from .grading import VerdictImporter, build_bundle, claims_for_grading
from .manifest import ManifestLoader, canonical_json
from .report import ReportBuilder
from .runner import CampaignRunner
from .store import RunStore


def _print_json(value: object) -> None:
    print(canonical_json(value).decode())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reviewbench", description="Run and compare AI code reviewers."
    )
    parser.add_argument("--state-dir", type=Path, default=Path(".siftbench"))
    commands = parser.add_subparsers(dest="command")
    for name in ("doctor", "plan"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--run-id")
    run.add_argument("--fixture", type=Path, required=True)
    status = commands.add_parser("status")
    status.add_argument("--run-id", required=True)
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
            _print_json({"python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
                         "manifest_digest": manifest.manifest_digest, "pairs": len(manifest.pairs())})
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
            if manifest.settling_seconds:
                raise ValueError("Local fixtures require settling_seconds=0")
            _print_json(CampaignRunner(store, FixtureTransport.load(args.fixture)).step(manifest))
        elif args.command == "status":
            store.load_manifest(args.run_id)
            _print_json(store.statuses(args.run_id))
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
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"reviewbench: {error}", file=sys.stderr)
        return 1
    return 0
