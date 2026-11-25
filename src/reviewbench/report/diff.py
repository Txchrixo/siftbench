"""Display the source hunk recorded with a GitHub review comment."""

import re
from html import escape
from itertools import zip_longest


_HEADER = re.compile(r"@@ -(\d{1,9})(?:,\d{1,9})? \+(\d{1,9})(?:,\d{1,9})? @@.*")
_KINDS = {"+": "addition", "-": "deletion", " ": "context"}


def _numbered(hunk: str) -> list[tuple[str, str, str, str]]:
    lines = hunk.splitlines()
    header = _HEADER.fullmatch(lines[0]) if lines else None
    if not header:
        return []
    old, new = map(int, header.groups())
    result = []
    for text in lines[1:]:
        marker = text[:1]
        if marker not in _KINDS:
            result.append(("", "", "", text))
            continue
        before = "" if marker == "+" else str(old)
        after = "" if marker == "-" else str(new)
        result.append((marker, before, after, text[1:]))
        old += marker != "+"
        new += marker != "-"
    return result


def _annotations(anchors: dict, before: str, after: str) -> str:
    links = [*anchors.get(("LEFT", before), ()), *anchors.get(("RIGHT", after), ())]
    return f'<div class="diff-annotations">{"".join(links)}</div>' if links else ""


def render_diff(hunk: str, locator: str, side: str = "RIGHT", *, label: str = "Captured GitHub diff", anchors: dict | None = None) -> str:
    lines = _numbered(hunk)
    if not lines:
        return f'<pre class="diff-raw">{escape(hunk)}</pre>'
    target = locator.rpartition(":")[2]
    result = [f'<div class="diff-lines" role="region" tabindex="0" aria-label="{escape(label, quote=True)}">']
    for marker, before, after, text in lines:
        if not marker:
            result.append(f'<code class="diff-note">{escape(text)}</code>')
            continue
        line = before if side == "LEFT" else after
        selected = " is-reported" if line and target == line else ""
        result.append(f'<div class="diff-line {_KINDS[marker]}{selected}">'
                      f'<span class="diff-number" aria-hidden="true">{before}</span>'
                      f'<span class="diff-number" aria-hidden="true">{after}</span>'
                      f'<code><span class="diff-marker">{marker}</span>{escape(text)}</code></div>')
        if anchors:
            result.append(_annotations(anchors, before, after))
    result.append("</div>")
    return "".join(result)


def _split(hunk: str, label: str, anchors: dict) -> str:
    result = [f'<div class="diff-lines split-lines" role="region" tabindex="0" aria-label="{escape(label, quote=True)}">',
              '<div class="split-row split-head"><span>Before</span><span>After</span></div>']
    removed = []
    added = []

    def cell(change: tuple[str, str, str] | None) -> str:
        if change is None:
            return '<span class="split-cell"></span>'
        marker, number, text = change
        return (f'<span class="split-cell {_KINDS[marker]}"><span class="diff-number" aria-hidden="true">{number}</span>'
                f'<code><span class="diff-marker">{marker}</span>{escape(text)}</code></span>')

    def row(left=None, right=None) -> None:
        result.append(f'<div class="split-row">{cell(left)}{cell(right)}</div>')
        before = _annotations(anchors, left[1] if left else "", "")
        after = _annotations(anchors, "", right[1] if right else "")
        if before or after:
            result.append(f'<div class="split-row split-annotations"><div>{before}</div><div>{after}</div></div>')

    def flush() -> None:
        for left, right in zip_longest(removed, added):
            row(left, right)
        removed.clear()
        added.clear()

    for marker, before, after, text in _numbered(hunk):
        if marker == "-":
            removed.append((marker, before, text))
        elif marker == "+":
            added.append((marker, after, text))
        else:
            flush()
            if marker:
                row((marker, before, text), (marker, after, text))
            else:
                result.append(f'<code class="diff-note">{escape(text)}</code>')
    flush()
    result.append('</div>')
    return "".join(result)


def render_file_patch(patch: str, path: str, anchors: dict | None = None) -> str:
    anchors = anchors or {}
    hunks = re.split(r"(?m)(?=^@@ -[0-9])", patch)
    if len(hunks) == 1:
        return f'<pre class="diff-raw">{escape(patch)}</pre>'
    unified = []
    split = []
    for index, hunk in enumerate(hunks[1:], start=1):
        label = f"{path}, hunk {index}"
        unified.append(render_diff(hunk, "", label=label, anchors=anchors))
        split.append(_split(hunk, label, anchors))
    return f'<div class="unified-patch">{"".join(unified)}</div><div class="split-patch">{"".join(split)}</div>'
