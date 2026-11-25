from reviewbench.report.diff import render_diff


def test_diff_highlights_the_reported_head_line_and_escapes_code():
    rendered = render_diff("@@ -8,2 +10,2 @@\n old context\n-removed\n+<script>alert(1)</script>", "reports.py:11")
    assert rendered.count("is-reported") == 1
    assert 'class="diff-line addition is-reported"' in rendered
    assert 'class="diff-line deletion"' in rendered
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in rendered
    assert '<script>' not in rendered
    assert 'class="diff-line deletion is-reported"' in render_diff("@@ -8 +8 @@\n-old\n+new", "reports.py:8", "LEFT")


def test_unrecognized_hunks_use_plain_escaped_text():
    assert render_diff("<img src=x>", "") == '<pre class="diff-raw">&lt;img src=x&gt;</pre>'
