"""Small inline icons for report navigation and evidence."""

_PATHS = {
    "ask": "M3 3h10v8H8l-3 2v-2H3zm4.25 2.75a1.25 1.25 0 1 1 1.9 1.07c-.43.25-.65.48-.65.93m0 1.5h.01",
    "agent": "M3 5.5h10v7H3zM8 3v2.5M1.5 8v2m13-2v2M6 8.5h.01m4 0h.01",
    "copy": "M6 2.5h7.5V10H6zM3 6v7.5h7.5",
    "check": "m3.5 8 3 3 6-6",
    "close": "m4 4 8 8m0-8-8 8",
    "overview": "M2.5 2.5h4v4h-4zm7 0h4v4h-4zm-7 7h4v4h-4zm7 0h4v4h-4z",
    "coverage": "M2.5 3h11v10h-11zM2.5 7h11m-7 0v6m3.5-6v6",
    "compare": "M5.5 3.5 2 7l3.5 3.5m-3.5-3.5h9m-.5 5.5L14 9l-3.5-3.5M14 9H5",
    "pr": "M4 5v6m8 0V7a3 3 0 0 0-3-3H8m2-2L8 4l2 2M4 2a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Zm0 9a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Zm8 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3Z",
    "diff": "M4 2h5l3 3v9H4zM9 2v3h3M6 8h4m-2-2v4m-2 2h4",
    "file": "M4 2h5l3 3v9H4zM9 2v3h3M6 8h4m-4 3h4",
    "commit": "M2 8h3m6 0h3M8 5a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z",
    "publication": "M3 2.5h10v11H3zM5.5 8 7 9.5l3.5-3.5",
    "evidence": "M8 2 2.5 4v4.5c0 2.5 2.5 4.5 5.5 6 3-1.5 5.5-3.5 5.5-6V4L8 2Zm-2.5 6L7 9.5l3.5-3.5",
    "source": "m5.5 4-4 4 4 4m5-8 4 4-4 4M9 3 7 13",
    "external": "M9.5 2.5h4v4m0-4-6 6m4.5 0v4h-8v-8h4",
    "back": "m7 3.5-4.5 4.5L7 12.5M2.5 8h11",
}


def icon(name: str) -> str:
    return ('<svg class="report-icon" viewBox="0 0 16 16" fill="none" aria-hidden="true" focusable="false">'
            f'<path d="{_PATHS[name]}" stroke="currentColor" stroke-width="1.35" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>')
