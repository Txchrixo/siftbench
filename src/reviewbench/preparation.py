"""Prepare minimal, reproducible Git repositories without publishing any refs."""

import json
import re
from datetime import datetime
from pathlib import Path
from shutil import copytree
from tempfile import TemporaryDirectory, TemporaryFile

from .discovery import PROTOCOL, PR_BODY, source_repo, ancestor, correction_patch, git, license_at, load_sources, revision
from .grading import SEVERITIES
from .manifest import _repo, canonical_json, digest, identifier


_COMMIT_ENV = {f"GIT_{role}_{key}": value for role in ("AUTHOR", "COMMITTER")
               for key, value in (("NAME", "SiftBench"), ("EMAIL", "benchmark@example.invalid"),
                                  ("DATE", "2000-01-01T00:00:00+00:00"))}


def approvals(catalog: dict, rubric: dict) -> tuple[dict, dict]:
    """Validate approvals and match them to the exact discovered inputs."""
    if rubric.get("schema_version") != 2:
        raise ValueError("Discovered corpora require a version 2 rubric")
    cases = {(row["case_id"], row["condition"]): row for row in catalog["cases"]}
    approved, expected, ids = {}, {}, set()
    for row in rubric["cases"]:
        key = (identifier(row.get("case_id"), "case_id"), identifier(row.get("condition"), "condition"))
        if key not in cases or key in approved or row.get("kind") != cases[key]["kind"]:
            raise ValueError("Rubric approval must match a unique discovered case and kind")
        name, timestamp = row.get("approved_by"), row.get("approved_at")
        if not isinstance(name, str) or not name.strip() or not isinstance(timestamp, str):
            raise ValueError(f"{key[0]} requires approved_by and approved_at")
        if datetime.fromisoformat(timestamp).utcoffset() is None:
            raise ValueError(f"{key[0]} approval timestamp requires a time zone")
        if any(row.get(field) != cases[key].get(field) for field in ("base_sha", "head_sha", "fix_sha")):
            raise ValueError(f"{key[0]} approval does not match the source revisions")
        if "neutral_title" in row and type(row["neutral_title"]) is not bool:
            raise ValueError("neutral_title must be a boolean")
        approved[key] = row
        expected[key] = []
    if cases.keys() != approved.keys() or not approved:
        raise ValueError("Every selected case requires recorded approval before preparation")
    for item in rubric["expected"]:
        key = (item.get("case_id"), item.get("condition"))
        expected_id = identifier(item.get("expected_id"), "expected_id")
        criteria = item.get("matching_criteria")
        if key not in expected or (key, expected_id) in ids or not isinstance(criteria, str) or not criteria.strip():
            raise ValueError("Expected a unique reference ID and matching criteria for an approved case")
        if item.get("severity") is not None and item["severity"] not in SEVERITIES:
            raise ValueError("Invalid reference severity")
        ids.add((key, expected_id))
        expected[key].append(criteria)
    for key, row in cases.items():
        if row["kind"] == "reference" and not expected[key] or row["kind"] == "clean" and expected[key]:
            raise ValueError("Reference cases need findings and clean cases require zero findings")
    return approved, expected


def draft_rubric(catalog: dict, config: dict, root: Path) -> dict:
    sources = load_sources(config, root)
    cases = []
    for case in catalog["cases"]:
        row = {key: case[key] for key in ("case_id", "condition", "kind", "base_sha", "head_sha")}
        row.update(approved_by=None, approved_at=None)
        if sha := case.get("fix_sha"):
            path = sources[source_repo(case)][0]
            patch = correction_patch(path, sha).decode("utf-8", errors="replace")
            files = git(path, "diff", "--no-renames", "--name-only", "-z", f"{sha}^1", sha, "--").stdout.decode().split("\0")
            row.update(fix_sha=sha, correction_message=git(path, "show", "-s", "--format=%B", sha).stdout.decode().strip(),
                       correction_diff=patch, test_files=[file for file in files if re.search(r"test|spec", file, re.I)])
        cases.append(row)
    return {"schema_version": 2, "cases": cases, "expected": []}


def _title(case: dict, approval: dict, criteria: list[str], correction_message: str) -> str:
    normalize = lambda value: " ".join(re.findall(r"\w+", value.lower()))
    title = "Proposed application change" if approval.get("neutral_title") else case["title"]
    exposure = normalize(f"{title} {PR_BODY}")
    if any(normalize(text) and normalize(text) in exposure
           for text in [*criteria, *correction_message.splitlines()]):
        raise ValueError(f'{case["case_id"]}: review text exposes the rubric or correction. Approve a neutral title.')
    return title


def _commit(path: Path, tree: str, message: str, parent: str | None = None) -> str:
    args = ["commit-tree", tree]
    if parent:
        args.extend(("-p", parent))
    return git(path, *args, "-m", message, extra_env=_COMMIT_ENV).stdout.decode().strip()


def _check_cross_case(rows: list[dict], source: Path, work: Path) -> None:
    index = {"GIT_INDEX_FILE": str(work / "check-index")}
    for case in rows:
        fix = case.get("fix_sha")
        if not fix:
            continue
        patch = correction_patch(source, fix)
        for other in rows:
            for sha in {other["base_sha"], other["head_sha"]}:
                if ancestor(source, fix, sha):
                    raise ValueError(f'{other["case_id"]} exposes the correction for {case["case_id"]}')
                # A revert can restore the original base. The reviewer already sees
                # that base as one side of this case's diff.
                if other is case and sha == case["base_sha"]:
                    continue
                git(source, "read-tree", sha, extra_env=index)
                if patch and not git(source, "apply", "--cached", "--reverse", "--check", "-",
                                     data=patch, extra_env=index, check=False).returncode:
                    raise ValueError(f'{other["case_id"]} appears to expose the correction for {case["case_id"]}')


def prepare_repos(catalog: dict, targets: list[dict], rubric_bytes: bytes, config: dict,
                  source_root: Path, output: Path, *, run_id: str, repeats: int) -> dict:
    from .corpus import prepare_corpus

    if catalog.get("protocol") != PROTOCOL:
        raise ValueError("Repository preparation requires a catalog from discover-cases")
    if catalog.get("selection", {}).get("deficits"):
        raise ValueError("Resolve the catalog selection deficits before preparing review repositories")
    if output.exists():
        raise ValueError("Repository output must be a new directory")
    if type(repeats) is not int or not 1 <= repeats <= 100:
        raise ValueError("Repeats must be an integer from 1 to 100")
    identifier(run_id, "run_id")
    rubric = json.loads(rubric_bytes)
    approved, expected = approvals(catalog, rubric)
    sources = load_sources(config, source_root)
    cases, compiled = {}, []
    for case in catalog["cases"]:
        cases.setdefault(case["isolation_group"], []).append(case)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".siftbench-prepare-", dir=output.parent) as name:
        work = Path(name)
        staged = work / "output"
        staged.mkdir()
        for group, rows in sorted(cases.items()):
            repos = {source_repo(case) for case in rows}
            if len(repos) != 1:
                raise ValueError("Each isolation group must refer to one source project")
            source = sources[repos.pop()][0]
            trees, titles = {}, {}
            for case in rows:
                key = (case["case_id"], case["condition"])
                correction = git(source, "show", "-s", "--format=%B", case["fix_sha"]).stdout.decode() if case.get("fix_sha") else ""
                titles[key] = _title(case, approved[key], expected[key], correction)
                for sha in {case["base_sha"], case["head_sha"]} - trees.keys():
                    revision(source, sha)
                    if license_at(source, sha)[0] != case["license"]:
                        raise ValueError(f'{case["case_id"]}: pinned license mismatch')
                    trees[sha] = git(source, "rev-parse", f"{sha}^{{tree}}").stdout.decode().strip()
            _check_cross_case(rows, source, work)
            template = work / f"{identifier(group, 'isolation_group')}.git"
            git(work, "init", "--bare", "--template=", "--initial-branch=main", str(template))
            tree_list = "\n".join(sorted(set(trees.values()))).encode() + b"\n"
            with TemporaryFile(dir=work) as pack:
                git(source, "pack-objects", "--stdout", "--revs", "--no-reuse-delta", data=tree_list, stdout=pack)
                pack.seek(0)
                git(template, "index-pack", "--stdin", stdin=pack)
            for index, case in enumerate(sorted(rows, key=lambda row: (row["date"], row["case_id"]))):
                key = (case["case_id"], case["condition"])
                opaque = digest(canonical_json([*key, case["base_sha"], case["head_sha"]]))[:24]
                base_ref = f"siftbench-base/{opaque}"
                base = _commit(template, trees[case["base_sha"]], "Pinned base")
                if index == 0:
                    git(template, "update-ref", "refs/heads/main", base)
                git(template, "update-ref", f"refs/heads/{base_ref}", base)
                heads = [_commit(template, trees[case["head_sha"]], f"Review input {opaque}/{repeat}", base)
                         for repeat in range(1, repeats + 1)]
                for repeat, head in enumerate(heads, start=1):
                    git(template, "update-ref", f"refs/heads/siftbench-input/{opaque}/{repeat}", head)
                compiled.append({"case_id": key[0], "condition": key[1], "base_ref": base_ref, "base_sha": base,
                                 "head_sha": heads[0], "repeat_heads": heads, "title": titles[key]})
            git(template, "fsck", "--full", "--no-reflogs")
            for target in targets:
                if target.get("isolation_group") != group:
                    continue
                repo = _repo(target["repository"])
                destination = staged / (repo + ".git")
                destination.parent.mkdir(exist_ok=True)
                if destination.exists():
                    raise ValueError("Duplicate private target repository")
                copytree(template, destination)
        inputs = {"schema_version": 1, "run_id": run_id, "repeats": repeats, "cases": compiled}
        manifest = prepare_corpus(catalog, targets, rubric_bytes, run_id, repeats, inputs)
        from .analysis import freeze_corpus
        for filename, value in (("review-inputs.json", inputs), ("targets.json", targets),
                                ("corpus-lock.json", freeze_corpus(manifest, rubric_bytes))):
            (staged / filename).write_bytes(canonical_json(value) + b"\n")
        (staged / "campaign.json").write_bytes(manifest.json + b"\n")
        staged.rename(output)
    return {"output": str(output), "repositories": len(targets), "attempts": len(manifest.pairs())}
