"""Render a small, inert subset of review Markdown for the offline report."""

import re
from html import escape


_METADATA = (r"<!--[ \t]*(?:/?proofline:|coderabbit[-:]|autofix_checkbox_)"
             r"(?:(?!-->)[^\r\n])*-->")
_METADATA_RUN = re.compile(rf"(?:{_METADATA}\s*)+")
_GREPTILE_BADGE = re.compile(
    r'\A<a href=""><img alt="(P[0-9]+)" src="https://greptile-static-assets\.s3\.amazonaws\.com/'
    r'badges/[^"<>]+"(?: align="top")?></a>\s*'
)
_PROOFLINE_TITLE = re.compile(r"\*\*(Info|Suggestion|Warning|Serious|Critical)\*\*\s*[·–—-]\s*(.+)")
_PROOFLINE_FOOTER = re.compile(
    r"\[View (?:finding|full review) in Proofline\]\(https://proofline\.sh/[^)\n]+\)"
)
_CODERABBIT_PICTURE = re.compile(
    r'<picture>[^\r\n]*?https://storage\.googleapis\.com/coderabbit_public_assets/[^\r\n]*?</picture>'
)
_CODERABBIT_SEVERITY = re.compile(r"\*\*\s*(Critical|Major|Minor|Trivial|High|Medium|Low|Info)\s*\*\*(?:\s*·.*)?")
_INLINE = re.compile(rf"(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\n()]*\)|{_METADATA})")
_LIST_ITEM = re.compile(r"\s*([-+*]|[0-9]+\.)\s+(.+)")
_SUMMARY = re.compile(r"<summary>(.+)</summary>")


def inline(text: str) -> str:
    parts = _INLINE.split(text)
    for index in range(1, len(parts), 2):
        token = parts[index]
        if token.startswith("`"):
            parts[index] = f"<code>{escape(token[1:-1])}</code>"
        elif token.startswith("**"):
            parts[index] = f"<strong>{escape(token[2:-2])}</strong>"
        elif token.startswith("<!--"):
            parts[index] = ""
        else:
            parts[index] = inline(token[1:token.index("](")])
    return "".join(escape(part) if index % 2 == 0 else part for index, part in enumerate(parts))


def review_text(raw: str) -> tuple[str, str, str]:
    """Return the display title, provider severity and readable body."""
    text = raw.lstrip()
    coderabbit = text.startswith("<!-- coderabbit-ai-generated:")
    if metadata := _METADATA_RUN.match(text):
        text = text[metadata.end():]
    badge = _GREPTILE_BADGE.match(text)
    severity = badge[1] if badge else ""
    if badge:
        text = text[badge.end():]
    first, _, remaining = text.partition("\n")
    if coderabbit and (level := _CODERABBIT_SEVERITY.fullmatch(_CODERABBIT_PICTURE.sub("", first).strip())):
        severity = level[1]
        text = remaining.lstrip()
        first, _, remaining = text.partition("\n")
    heading = _PROOFLINE_TITLE.fullmatch(first.strip())
    if heading:
        return heading[2], heading[1], remaining.strip()
    bold = re.fullmatch(r"\*\*(.+?)\*\*", first.strip())
    if bold:
        return bold[1], severity, remaining.strip()
    return "", severity, text.strip()


def blocks(text: str) -> str:
    """Format prose and known disclosures without activating supplied HTML."""
    output = []
    paragraph = []
    list_tag = ""
    fence = None
    code = []
    disclosures = 0
    summary_line = -1
    lines = text.splitlines()

    def flush_paragraph() -> None:
        if paragraph:
            output.append(f"<p>{inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            output.append(f"</{list_tag}>")
            list_tag = ""

    for index, line in enumerate(lines):
        if index == summary_line:
            continue
        stripped = line.strip()
        if fence:
            if re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + ",}", stripped):
                output.append(f"<pre><code>{escape(chr(10).join(code))}</code></pre>")
                code.clear()
                fence = None
            else:
                code.append(line)
        elif marker := re.fullmatch(r"(`{3,}|~{3,})[\w+.-]*", stripped):
            flush_paragraph()
            close_list()
            fence = marker[1]
        elif (stripped == "<details>" and index + 1 < len(lines)
              and (summary := _SUMMARY.fullmatch(lines[index + 1].strip()))):
            flush_paragraph()
            close_list()
            label = _CODERABBIT_PICTURE.sub("", summary[1]).strip()
            label = "Reviewer details" if label.casefold() == "details" else label
            output.append(f'<details class="review-disclosure"><summary>{inline(label)}</summary>'
                          '<div class="disclosure-copy">')
            disclosures += 1
            summary_line = index + 1
        elif stripped == "</details>" and disclosures:
            flush_paragraph()
            close_list()
            output.append("</div></details>")
            disclosures -= 1
        elif _METADATA_RUN.fullmatch(stripped) or _PROOFLINE_FOOTER.fullmatch(stripped):
            continue
        elif item := _LIST_ITEM.fullmatch(line):
            flush_paragraph()
            tag = "ol" if item[1].endswith(".") else "ul"
            if tag != list_tag:
                close_list()
                list_tag = tag
                start = f' start="{item[1][:-1]}"' if tag == "ol" and item[1] != "1." else ""
                output.append(f"<{tag}{start}>")
            output.append(f"<li>{inline(item[2])}</li>")
        else:
            close_list()
            if stripped:
                paragraph.append(stripped)
            else:
                flush_paragraph()
    if fence:
        output.append(f"<pre><code>{escape(chr(10).join(code))}</code></pre>")
    flush_paragraph()
    close_list()
    output.extend(["</div></details>"] * disclosures)
    return "".join(output)
