import argparse
import sqlite3
import sys
from pathlib import Path

from .manifest import ManifestLoader, canonical_json


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
    args = parser.parse_args(argv)
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
        else:
            parser.print_help()
    except (ValueError, OSError, sqlite3.Error) as error:
        print(f"reviewbench: {error}", file=sys.stderr)
        return 1
    return 0
