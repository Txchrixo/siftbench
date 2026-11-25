"""Render a small, inert subset of review Markdown for the offline report."""

import re
from html import escape

from .icons import icon

_METADATA = (r"<!--[ \t]*(?:/?proofline:|coderabbit[-:]|autofix_checkbox_|ai-agent-prompt:|"
             r"fingerprinting:|cr-indicator-types:|cr-comment:|This is an auto-generated comment by CodeRabbit)"
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
_REVIEW_SOURCE = re.compile(r"_Source: ([^_<>]+)_")
_AGENT_PROMPT = re.compile(
    r"[ \t]*<details>[ \t]*\n[ \t]*<summary>(?P<label>[^\n]+)</summary>[ \t]*\n"
    r"[\s]*<dl>\s*<dd>\s*"
    r"(?P<fence>`{3,})[\w+.-]*[ \t]*\n(?P<prompt>[\s\S]*?)\n"
    r"[ \t]*(?P=fence)[ \t]*\n\s*</dd>\s*</dl>\s*</details>[ \t]*(?=\n|$)"
)


def _prompt_block(prompt: str) -> str:
    return ('<details class="review-prompt"><summary>'
            f'{icon("agent")}Prompt to fix issue</summary><div class="prompt-body">'
            f'<pre tabindex="0" role="region" aria-label="CodeRabbit prompt"><code>{escape(prompt)}</code></pre>'
            '<button type="button" class="prompt-copy" aria-label="Copy prompt" title="Copy prompt" hidden>'
            f'{icon("copy")}</button></div><p class="prompt-status" role="status"></p></details>')


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
    handled_through = -1
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
        if index <= handled_through:
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
            if label == "Prompt to fix issue":
                prompt = _AGENT_PROMPT.match("\n".join(lines[index:]))
                if prompt:
                    output.append(_prompt_block(prompt["prompt"]))
                    handled_through = index + prompt[0].count("\n")
                    continue
            label = "Reviewer details" if label.casefold() == "details" else label
            output.append(f'<details class="review-disclosure"><summary>{inline(label)}</summary>'
                          '<div class="disclosure-copy">')
            disclosures += 1
            handled_through = index + 1
        elif stripped == "</details>" and disclosures:
            flush_paragraph()
            close_list()
            output.append("</div></details>")
            disclosures -= 1
        elif _METADATA_RUN.fullmatch(stripped) or _PROOFLINE_FOOTER.fullmatch(stripped):
            continue
        elif source := _REVIEW_SOURCE.fullmatch(stripped):
            flush_paragraph()
            close_list()
            output.append(f'<p class="review-attribution">Source · {escape(source[1])}</p>')
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
