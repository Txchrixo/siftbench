from reviewbench.report.diff import render_diff, render_file_patch


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


def test_file_patch_keeps_exact_old_and_new_comment_anchors_in_both_views():
    patch = "@@ -8 +10 @@\n-old\n+<user>\n@@ -21 +23,2 @@\n same\n+extra\n"
    anchors = {("LEFT", "8"): ["left-comment"], ("RIGHT", "10"): ["right-comment"],
               ("RIGHT", "24"): ["second-hunk"], ("RIGHT", "8"): ["wrong-side"]}
    unified, split = render_file_patch(patch, "a.py", anchors).split('<div class="split-patch">')
    for view in (unified, split):
        assert "wrong-side" not in view
        assert "<user>" not in view
        assert "&lt;user&gt;" in view
        assert view.count("left-comment") == view.count("right-comment") == view.count("second-hunk") == 1
        assert view.index("second-hunk") > view.index("extra")
    assert '<div class="split-row split-annotations"><div><div class="diff-annotations">left-comment</div></div>' in split
