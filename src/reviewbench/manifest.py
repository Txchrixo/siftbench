import hashlib
import json
import re
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class ManifestError(ValueError):
    """The supplied campaign configuration is invalid."""


_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_REPOSITORY = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9-]*/[a-zA-Z0-9_.-]+")
_REF = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_./-]{0,199}")
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PUBLISHER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9-]*(?:\[bot\])?")


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identifier(value: Any, key: str) -> str:
    return _pattern(value, _SLUG, key)


def _pattern(value: Any, pattern: re.Pattern[str], key: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ManifestError(f"Invalid {key}")
    return value


def _repo(value: Any) -> str:
    repo = _pattern(value, _REPOSITORY, "repository").lower()
    if repo.split("/")[1] in {".", ".."}:
        raise ManifestError("Invalid repository name")
    return repo


def _ref(value: Any) -> str:
    ref = _pattern(value, _REF, "branch ref")
    if ".." in ref or any(
        not component or component.startswith(".") or component.endswith((".", ".lock"))
        for component in ref.split("/")
    ):
        raise ManifestError("Invalid branch ref")
    return ref


def _fields(data: Any, required: set[str], optional: set[str] = frozenset()) -> None:
    if not isinstance(data, dict) or required - data.keys() or data.keys() - required - optional:
        raise ManifestError(f"Expected required fields: {', '.join(sorted(required))}")


def _text(value: Any, key: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ManifestError(f"Invalid {key}")
    return value


def _integer(value: Any, key: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ManifestError(f"{key} must be an integer from {lower} to {upper}")
    return value


@dataclass(frozen=True)
class CaseVariant:
    case_id: str
    condition: str
    title: str
    body: str
    base_ref: str
    base_sha: str
    head_sha: str
    isolation_group: str | None = None
    repeat_heads: tuple[str, ...] = ()
    provenance: dict | None = None

    @property
    def input_digest(self) -> str:
        data = asdict(self)
        _omit_case_defaults(data)
        return digest(canonical_json(data))


@dataclass(frozen=True)
class Target:
    provider_id: str
    condition: str
    repository: str
    publisher: str
    trigger: str = "automatic"
    completion_check: str | None = None
    completion_policy: str | None = None
    vendor_version: str | None = None
    isolation_group: str | None = None


@dataclass(frozen=True)
class ExperimentPair:
    pair_id: str
    variant_id: str
    variant: CaseVariant
    target: Target
    repeat_index: int

    @property
    def head_sha(self) -> str:
        return self.variant.repeat_heads[self.repeat_index] if self.variant.repeat_heads else self.variant.head_sha


def _omit_case_defaults(data: dict) -> None:
    for key in ("isolation_group", "repeat_heads", "provenance"):
        if not data[key]:
            del data[key]


@dataclass(frozen=True)
class Manifest:
    run_id: str
    allowed_repositories: tuple[str, ...]
    rubric_digest: str
    timeout_seconds: int
    settling_seconds: int
    repeats: int
    cases: tuple[CaseVariant, ...]
    targets: tuple[Target, ...]

    @property
    def json(self) -> bytes:
        data = asdict(self)
        for case in data["cases"]:
            _omit_case_defaults(case)
        for target in data["targets"]:
            for optional in ("completion_policy", "vendor_version", "isolation_group"):
                if target[optional] is None:
                    del target[optional]
        return canonical_json(data)

    @property
    def manifest_digest(self) -> str:
        return digest(self.json)

    def pairs(self) -> tuple[ExperimentPair, ...]:
        return tuple(
            ExperimentPair(
                digest(canonical_json([self.run_id, case.case_id, case.condition, target.provider_id, index])),
                digest(canonical_json([self.run_id, case.case_id, case.condition])),
                case, target, index,
            )
            for case in self.cases
            for target in self.targets if (target.condition, target.isolation_group) == (case.condition, case.isolation_group)
            for index in range(self.repeats)
        )


class ManifestLoader:
    @staticmethod
    def load(path: Path) -> Manifest:
        try:
            with path.open("rb") as file:
                return ManifestLoader.parse(json.load(file) if path.suffix.lower() == ".json" else tomllib.load(file))
        except (OSError, ValueError) as error:
            if isinstance(error, ManifestError):
                raise
            raise ManifestError(f"Cannot read campaign manifest: {error}") from error

    @staticmethod
    def parse(data: Any) -> Manifest:
        _fields(data, {"run_id", "allowed_repositories", "rubric_digest", "cases", "targets"},
                {"timeout_seconds", "settling_seconds", "repeats"})
        for key in ("allowed_repositories", "cases", "targets"):
            if not isinstance(data[key], list) or not data[key]:
                raise ManifestError(f"{key} must be a nonempty array")
        allowed = tuple(sorted({_repo(repo) for repo in data["allowed_repositories"]}))
        cases = tuple(sorted((_case(item) for item in data["cases"]), key=lambda case: (case.case_id, case.condition)))
        targets = tuple(sorted((_target(item) for item in data["targets"]), key=lambda target: (target.condition, target.provider_id, target.isolation_group or "")))
        timeout = _integer(data.get("timeout_seconds", 1800), "timeout_seconds", 1, 86400)
        settling = _integer(data.get("settling_seconds", min(30, timeout - 1)), "settling_seconds", 0, timeout - 1)
        if len({(case.case_id, case.condition) for case in cases}) != len(cases):
            raise ManifestError("Duplicate case and condition")
        if len({(target.provider_id, target.condition, target.isolation_group) for target in targets}) != len(targets):
            raise ManifestError("Duplicate provider, condition and isolation group")
        if len({target.repository for target in targets}) != len(targets):
            raise ManifestError("Each provider and condition requires a separate test repository")
        if any(target.repository not in allowed for target in targets):
            raise ManifestError("Target repository is outside the allowlist")
        if {(case.condition, case.isolation_group) for case in cases} != {(target.condition, target.isolation_group) for target in targets}:
            raise ManifestError("Each condition and isolation group requires cases and targets")
        repeats = _integer(data.get("repeats", 1), "repeats", 1, 100)
        if any(case.repeat_heads and len(case.repeat_heads) != repeats for case in cases):
            raise ManifestError("Each prepared case requires one pinned head per repeat")
        return Manifest(
            identifier(data["run_id"], "run_id"), allowed, _pattern(data["rubric_digest"], _DIGEST, "rubric_digest"),
            timeout, settling, repeats, cases, targets,
        )


def _case(data: Any) -> CaseVariant:
    _fields(data, {"case_id", "condition", "title", "body", "base_ref", "base_sha", "head_sha"},
            {"isolation_group", "repeat_heads", "provenance"})
    base = _pattern(data["base_sha"], _SHA, "base_sha")
    head = _pattern(data["head_sha"], _SHA, "head_sha")
    if base == head:
        raise ManifestError("Case base and head must differ")
    title = _text(data["title"], "PR title", 256)
    if "\r" in title or "\n" in title:
        raise ManifestError("PR title must fit on one line")
    heads = data.get("repeat_heads", [])
    if not isinstance(heads, list):
        raise ManifestError("Expected an array of pinned repeat heads")
    heads = tuple(_pattern(sha, _SHA, "repeat head") for sha in heads)
    if heads and (heads[0] != head or base in heads or len(set(heads)) != len(heads)):
        raise ManifestError("Repeat heads must be distinct and begin with the pinned head")
    provenance = data.get("provenance")
    if provenance is not None:
        _fields(provenance, {"source_pr", "base_sha", "head_sha", "title", "language", "date", "license", "license_url",
                             "reference_count"})
        for key, value in provenance.items():
            if key != "reference_count":
                _text(value, f"source {key}", 1024)
        _integer(provenance["reference_count"], "reference_count", 0, 1000)
        for key in ("base_sha", "head_sha"):
            _pattern(provenance[key], _SHA, f"source {key}")
    return CaseVariant(identifier(data["case_id"], "case_id"), identifier(data["condition"], "condition"),
                       title, _text(data["body"], "PR body", 60000), _ref(data["base_ref"]), base, head,
                       identifier(data["isolation_group"], "isolation_group") if "isolation_group" in data else None,
                       heads, provenance)


def _target(data: Any) -> Target:
    _fields(data, {"provider_id", "condition", "repository", "publisher"},
            {"trigger", "completion_check", "completion_policy", "vendor_version", "isolation_group"})
    trigger = data.get("trigger", "automatic")
    if trigger not in ("automatic", "manual"):
        raise ManifestError("trigger must be automatic or manual")
    check = data.get("completion_check")
    policy = data.get("completion_policy")
    version = data.get("vendor_version")
    if policy is not None and (policy != "review_or_check" or check is None):
        raise ManifestError("completion_policy must be review_or_check with a completion_check")
    return Target(identifier(data["provider_id"], "provider_id"), identifier(data["condition"], "condition"),
                  _repo(data["repository"]), _pattern(data["publisher"], _PUBLISHER, "publisher").lower(),
                  trigger, _text(check, "completion_check", 255) if check is not None else None, policy,
                  _text(version, "vendor_version", 120) if version is not None else None,
                  identifier(data["isolation_group"], "isolation_group") if "isolation_group" in data else None)
