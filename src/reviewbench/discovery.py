"""Discover comparable real PRs using local Git objects and exported PR metadata."""

import os
import re
import subprocess
from collections import Counter
from datetime import date
from pathlib import Path, PurePosixPath

from .manifest import digest


PROTOCOL = "multi-project-v1"
PR_BODY = "Review the proposed change in the pinned repository."
SHA = re.compile(r"[0-9a-f]{40}")
SLUG = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ROOT_LICENSE = re.compile(r"(?:licen[cs]e|copying|mit-license)(?:\.(?:md|txt|rst))?", re.I)
LANGUAGES = {".go": "Go", ".ts": "TypeScript", ".tsx": "TypeScript", ".py": "Python",
             ".java": "Java", ".rs": "Rust", ".js": "JavaScript", ".cs": "C#", ".rb": "Ruby",
             ".cpp": "C++", ".hpp": "C++", ".c": "C", ".kt": "Kotlin", ".swift": "Swift", ".php": "PHP"}
LICENSES = {
    "MIT": ("permission is hereby granted, free of charge", "the software is provided"),
    "Apache-2.0": ("apache license", "version 2.0, january 2004"),
    "BSD-2-Clause": ("redistributions of source code must retain", "redistributions in binary form"),
    "BSD-3-Clause": ("redistributions of source code must retain", "redistributions in binary form", "neither the name"),
    "ISC": ("permission to use, copy, modify, and/or distribute", "with or without fee"),
}
DEPENDENCY_FILES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "go.sum", "go.mod", "cargo.lock",
                    "poetry.lock", "uv.lock", "composer.lock", "gemfile.lock", "package.json",
                    "cargo.toml", "pyproject.toml", "requirements.txt", "pom.xml"}
EXCLUDED_DIRECTORIES = {"vendor", "node_modules", "dist", "generated", "__generated__", "third_party"}
FIX = re.compile(r"\b(?:fix(?:e[ds])?|bug|regression|revert)\b", re.I)
SENSITIVE = re.compile(r"\b(?:cve-\d+|ghsa-[\w-]+|exploits?|remote code execution|vulnerabilit(?:y|ies)|privilege escalation)\b", re.I)


def git(path: Path, *args: str, data: bytes | None = None, stdin=None, stdout=subprocess.PIPE, extra_env: dict | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    # Do not inherit repository, object store, or index overrides from the caller.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1"})
    env.update(extra_env or {})
    result = subprocess.run(["git", "--literal-pathspecs", "-C", str(path), "-c", "core.hooksPath=%(prefix)/dev/null",
                             "-c", "core.longpaths=true", "-c", "protocol.allow=never", *args],
                            input=data, stdin=stdin, stdout=stdout, stderr=subprocess.PIPE, env=env)
    if check and result.returncode:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"{path}: git {args[0]} failed ({message or result.returncode})")
    return result


def revision(path: Path, sha: object) -> str:
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise ValueError("Expected a full 40-character Git commit SHA")
    if git(path, "cat-file", "-t", sha).stdout.strip() != b"commit":
        raise ValueError(f"{sha} does not identify a commit")
    return sha


def ancestor(path: Path, base: str, head: str) -> bool:
    status = git(path, "merge-base", "--is-ancestor", base, head, check=False).returncode
    if status not in (0, 1):
        raise ValueError("Could not establish Git ancestry")
    return status == 0


def _license_text(path: Path, sha: str, name: str) -> str:
    if not git(path, "ls-tree", sha, "--", name).stdout.startswith((b"100644 blob ", b"100755 blob ")):
        raise ValueError("Expected a regular root license file")
    return " ".join(git(path, "show", f"{sha}:{name}").stdout.decode().lower().split())


def _license_id(body: str) -> str:
    if re.search(r"\b(?:affero|general public license|copyleft|all rights reserved by third parties)\b", body):
        raise ValueError("Non-permissive or mixed license")
    matches = {key for key, phrases in LICENSES.items() if all(phrase in body for phrase in phrases)}
    if "BSD-3-Clause" in matches:
        matches.discard("BSD-2-Clause")
    if len(matches) != 1:
        raise ValueError("Root license is absent, ambiguous or outside the permitted SPDX list")
    return matches.pop()


def license_at(path: Path, sha: str) -> tuple[str, str]:
    names = git(path, "ls-tree", "--name-only", "-z", sha).stdout.decode().split("\0")
    names = [name for name in names if ROOT_LICENSE.fullmatch(name)]
    if len(names) != 1:
        raise ValueError("Expected a single unambiguous root license file")
    name = names[0]
    body = _license_text(path, sha, name)
    # pypa/packaging explicitly grants the recipient a choice of two bundled licenses.
    notice = ("this software is made available under the terms of *either* of the licenses found in "
              "license.apache or license.bsd. contributions to this software is made under the terms of *both* these licenses.")
    if body == notice:
        apache = _license_id(_license_text(path, sha, "LICENSE.APACHE"))
        bsd = _license_id(_license_text(path, sha, "LICENSE.BSD"))
        if apache != "Apache-2.0" or bsd not in {"BSD-2-Clause", "BSD-3-Clause"}:
            raise ValueError("License alternatives do not match the permissive root license notice")
        return bsd, "LICENSE.BSD"
    return _license_id(body), name


def _activity(path: Path, base: str, head: str) -> tuple[list[tuple[int, str]], int]:
    raw = git(path, "diff", "--no-ext-diff", "--no-textconv", "--no-renames",
              "--numstat", "-z", base, head, "--").stdout.decode()
    changes, excluded = [], 0
    for row in filter(None, raw.split("\0")):
        added, removed, file = row.split("\t", 2)
        name = PurePosixPath(file)
        if not added.isdigit() or not removed.isdigit():
            raise ValueError("Binary file changes are outside this corpus")
        lines = int(added) + int(removed)
        if (name.name.lower() in DEPENDENCY_FILES or any(part.lower() in EXCLUDED_DIRECTORIES for part in name.parts)
                or re.search(r"(?:\.min\.[a-z]+|\.gen\.[a-z]+|_generated\.[a-z]+|\.pb\.go)$", name.name, re.I)):
            excluded += lines
        else:
            changes.append((lines, file))
    return changes, excluded


def correction_patch(path: Path, sha: str) -> bytes:
    sha = revision(path, sha)
    return git(path, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", f"{sha}^1", sha, "--").stdout


def _find_fix(path: Path, pull: dict, base: str, head: str, number: int, paths: list[str]) -> str | None:
    integration = revision(path, pull["merge_sha"]) if pull.get("merge_sha") else head
    if pull.get("fix_sha"):
        sha = revision(path, pull["fix_sha"])
        if not ancestor(path, integration, sha) or ancestor(path, sha, head) or ancestor(path, sha, base):
            raise ValueError("Correction must follow the merged change and be outside its review revisions")
        if SENSITIVE.search(git(path, "show", "-s", "--format=%B", sha).stdout.decode()):
            raise ValueError("Candidate falls outside ordinary functional review")
        return sha
    if not pull.get("merge_sha"):
        return None
    tip = revision(path, pull.get("search_tip") or git(path, "rev-parse", "HEAD").stdout.decode().strip())
    if not ancestor(path, integration, tip):
        raise ValueError("Search tip must descend from the merged PR")
    linked = re.compile(rf"(?<!\w)(?:#|/pull/){number}(?!\d)|\b{head[:12]}\w*")
    rows = git(path, "log", "-z", "--ancestry-path", "-n", "150", "--format=%H%x00%s%x00%b",
               f"{integration}..{tip}", "--", *paths).stdout.decode()
    fields = rows.removesuffix("\0").split("\0") if rows else []
    for sha, subject, body in zip(fields[::3], fields[1::3], fields[2::3], strict=True):
        if FIX.search(subject) and linked.search(f"{subject} {body}"):
            if SENSITIVE.search(f"{subject} {body}"):
                raise ValueError("Candidate falls outside ordinary functional review")
            return sha
    return None


def _candidate(path: Path, repo: str, pull: dict, limits: tuple[int, int, int, int]) -> dict:
    number = pull.get("number")
    title = pull.get("title")
    if type(number) is not int or number < 1 or not isinstance(title, str) or not title.strip():
        raise ValueError("Invalid source PR number or title")
    if SENSITIVE.search(title):
        raise ValueError("Candidate falls outside ordinary functional review")
    title = " ".join(title.split())
    if len(title) > 256:
        raise ValueError("Source PR title exceeds the review title limit")
    base, head = (revision(path, pull.get(key)) for key in ("base_sha", "head_sha"))
    if not ancestor(path, base, head) or base == head:
        raise ValueError("PR base must be the verified merge base of its pinned head")
    license_id, name = license_at(path, base)
    if license_at(path, head)[0] != license_id:
        raise ValueError("The PR changes its root license")
    changes, excluded = _activity(path, base, head)
    files = git(path, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z",
                base, head, "--").stdout.count(b"\0")
    lines = sum(lines for lines, _ in changes) + excluded
    min_files, max_files, min_lines, max_lines = limits
    if not min_files <= files <= max_files or not min_lines <= lines <= max_lines:
        raise ValueError(f"Outside size limits ({files} files, {lines} changed lines)")
    if excluded and excluded >= lines / 2:
        raise ValueError("Generated files or dependencies dominate the diff")
    lang = Counter()
    for count, file in changes:
        if language := LANGUAGES.get(PurePosixPath(file).suffix.lower()):
            lang[language] += count
    if not lang:
        raise ValueError("No supported source language in this diff")
    language = min(lang, key=lambda name: (-lang[name], name))
    fix = _find_fix(path, pull, base, head, number, [file for _, file in changes])
    group = repo.lower().replace("/", "-").replace(".", "-")
    if len(group) > 45:
        group = f"{group[:34]}-{digest(repo.lower().encode())[:10]}"
    head_date = git(path, "show", "-s", "--format=%cs", head).stdout.decode().strip()
    kind = pull.get("kind", "reference" if fix else None)
    if kind not in {"clean", "reference", "context"}:
        raise ValueError("No linked correction or nominated clean/context case")
    if kind == "reference" and not fix or kind == "clean" and fix:
        raise ValueError("Reference cases need a correction and clean candidates must have none")
    return {"case_id": f"{group}-{number}", "condition": "code_only", "isolation_group": group,
            "language": language, "title": title, "source_pr": f"https://github.com/{repo}/pull/{number}",
            "base_sha": base, "head_sha": head, "date": head_date, "license": license_id,
            "license_url": f"https://github.com/{repo}/blob/{base}/{name}",
            "changed_files": files, "changed_lines": lines, "kind": kind,
            **({"fix_sha": fix} if fix else {})}


def load_sources(config: dict, root: Path) -> dict[str, tuple[Path, list[dict]]]:
    sources = {}
    for item in config["repositories"]:
        repo = item["repository"]
        if not SLUG.fullmatch(repo) or any(part in (".", "..") for part in repo.split("/")) or repo.lower() in sources:
            raise ValueError("Invalid or duplicate source repository")
        path = (root / item["path"]).resolve(strict=True)
        git(path, "rev-parse", "--git-dir")
        pulls = item["pulls"]
        if not isinstance(pulls, list) or any(not isinstance(pull, dict) for pull in pulls):
            raise ValueError(f"{repo}: expected exported PR metadata")
        sources[repo.lower()] = (path, pulls)
    return sources


def source_repo(case: dict) -> str:
    found = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/pull/[1-9]\d*", case["source_pr"])
    if not found or not SLUG.fullmatch(found[1]):
        raise ValueError("Expected a canonical source PR URL")
    return found[1].lower()


def discover(config: dict, root: Path, *, target: int = 45, min_languages: int = 5, project_cap: int = 4,
             min_files: int = 1, max_files: int = 10, min_lines: int = 20, max_lines: int = 400,
             after: date | None = None) -> tuple[dict, str]:
    if min(target, min_languages, project_cap, min_files, min_lines) < 1 or min_files > max_files or min_lines > max_lines:
        raise ValueError("Invalid selection limits")
    eligible, rejected, seen = [], [], set()
    for repo, (path, pulls) in load_sources(config, root).items():
        for pull in pulls:
            label = f'{repo} #{pull.get("number", "?")}'
            try:
                key = (repo, pull.get("number"))
                if key in seen:
                    raise ValueError("Duplicate source PR")
                seen.add(key)
                eligible.append(_candidate(path, repo, pull, (min_files, max_files, min_lines, max_lines)))
            except (ValueError, OSError, UnicodeError) as error:
                rejected.append((label, str(error)))
    eligible.sort(key=lambda row: (row["date"], row["source_pr"]), reverse=True)
    if after:
        eligible.sort(key=lambda row: date.fromisoformat(row["date"]) < after)
    projects, languages, kinds = Counter(), Counter(), Counter()
    selected, clean_target = [], round(target * .27)
    while eligible and len(selected) < target:
        available = [row for row in eligible if projects[source_repo(row)] < project_cap]
        if not available:
            break
        clean_gap = clean_target - kinds["clean"]
        slots = target - len(selected)
        choice = min(available, key=lambda row: (
            (clean_gap <= 0 and row["kind"] == "clean") or (clean_gap >= slots and row["kind"] != "clean"),
            languages[row["language"]] > 0 and len(languages) < min_languages,
            row["kind"] != "clean" if clean_gap / slots > .27 else row["kind"] == "clean",
        ))
        eligible.remove(choice)
        selected.append(choice)
        projects[source_repo(choice)] += 1
        languages[choice["language"]] += 1
        kinds[choice["kind"]] += 1
    deficit = []
    if len(selected) != target:
        deficit.append(f"{len(selected)} of {target} PRs selected")
    if len(languages) < min_languages:
        deficit.append(f"{len(languages)} of {min_languages} languages represented")
    if target >= 4 and not .25 <= kinds["clean"] / max(1, len(selected)) <= .30:
        deficit.append(f"{kinds['clean']} of {len(selected)} candidates are nominated clean controls (target 25-30%)")
    catalog = {"schema_version": 1, "catalog_id": config.get("catalog_id", "siftbench-real-prs"), "protocol": PROTOCOL,
               "selection": {"target": target, "min_languages": min_languages, "project_cap": project_cap,
                             "deficits": deficit, "languages": dict(languages)},
            "cases": sorted(selected, key=lambda row: row["case_id"])}
    report = ["# Corpus candidate screening", "", *[f"- {line}" for line in deficit],
              "", "## Selected for human validation", ""]
    report.extend(f'- [{row["title"]}]({row["source_pr"]}) · {row["language"]} · {row["kind"]} candidate · '
                  f'{row["changed_files"]} files, {row["changed_lines"]} lines · {row["license"]}' for row in selected)
    report.extend(["", "## Excluded", ""])
    rejected.extend((row["source_pr"], "Project cap or selection target reached") for row in eligible)
    report.extend(f"- {label} · {' '.join(reason.split())}" for label, reason in sorted(rejected))
    return catalog, "\n".join(report) + "\n"
