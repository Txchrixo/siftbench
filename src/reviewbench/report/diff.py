"""Display the source hunk recorded with a GitHub review comment."""

import re
from html import escape


_HEADER = re.compile(r"@@ -(\d{1,9})(?:,\d{1,9})? \+(\d{1,9})(?:,\d{1,9})? @@.*")
_KINDS = {"+": "addition", "-": "deletion", " ": "context"}


def render_diff(hunk: str, locator: str, side: str = "RIGHT") -> str:
    lines = hunk.splitlines()
    header = _HEADER.fullmatch(lines[0]) if lines else None
    if not header:
        return f'<pre class="diff-raw">{escape(hunk)}</pre>'

    old, new = map(int, header.groups())
    target = locator.rpartition(":")[2]
    result = ['<div class="diff-lines" role="region" tabindex="0" aria-label="Captured GitHub diff">']
    for text in lines[1:]:
        marker = text[:1]
        if marker not in _KINDS:
            result.append(f'<code class="diff-note">{escape(text)}</code>')
            continue

        before = "" if marker == "+" else str(old)
        after = "" if marker == "-" else str(new)
        line = before if side == "LEFT" else after
        selected = " is-reported" if line and target == line else ""
        result.append(f'<div class="diff-line {_KINDS[marker]}{selected}">'
                      f'<span class="diff-number" aria-hidden="true">{before}</span>'
                      f'<span class="diff-number" aria-hidden="true">{after}</span>'
                      f'<code><span class="diff-marker">{marker}</span>{escape(text[1:])}</code></div>')
        old += marker != "+"
        new += marker != "-"
    result.append("</div>")
    return "".join(result)
