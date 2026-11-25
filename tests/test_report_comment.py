from reviewbench.report.comment import blocks, review_text


def test_review_metadata_becomes_a_readable_heading():
    greptile = ('<a href=""><img alt="P1" src="https://greptile-static-assets.s3.amazonaws.com/'
                'badges/p1.svg?v=9" align="top"></a> **History skips rows**\n\nUse a `cursor`.')
    proofline = ('<!-- proofline:review-comment -->\n<!-- proofline:finding-series=example -->\n'
                 '**Warning** · History skips rows\n\nUse a `cursor`.\n\n'
                 '[View finding in Proofline](https://proofline.sh/path)')
    assert review_text(greptile) == ("History skips rows", "P1", "Use a `cursor`.")
    title, severity, body = review_text(proofline)
    assert (title, severity) == ("History skips rows", "Warning")
    assert blocks(body) == '<p>Use a <code>cursor</code>.</p>'


def test_review_formatting_keeps_untrusted_content_inert():
    rendered = blocks('**Finding** on `db`.\n\n- <img src=x onerror=alert(1)>\n'
                      '- [Open](https://example.test/a)\n\n```html\n<script>run()</script>\n```')
    assert rendered == (
        '<p><strong>Finding</strong> on <code>db</code>.</p>'
        '<ul><li>&lt;img src=x onerror=alert(1)&gt;</li><li>Open</li></ul>'
        '<pre><code>&lt;script&gt;run()&lt;/script&gt;</code></pre>'
    )


def test_coderabbit_review_title_keeps_suggestion_and_hides_provider_markup():
    picture = '<picture><img src="https://storage.googleapis.com/coderabbit_public_assets/major.svg"></picture>'
    raw = (f"<!-- coderabbit-ai-generated:v1 -->\n\n**{picture} Major** · **Functional Correctness**\n\n"
           "**Keep the ID tie-breaker.**\n\nUse both fields.\n\n<!-- coderabbit-global-learning v1 sample -->\n"
           f"<details>\n<summary>{picture} Suggested change</summary>\n\nKeep the ID.\n</details>")
    title, severity, body = review_text(raw)
    assert (title, severity) == ("Keep the ID tie-breaker.", "Major")
    rendered = blocks(body)
    assert "Use both fields." in rendered and "<summary>Suggested change</summary>" in rendered
    assert "googleapis" not in rendered and "coderabbit-global-learning" not in rendered


def test_review_summary_hides_provider_markers_and_redundant_footer():
    raw = ('<!-- proofline:review-comment -->\n**Proofline** has 1 new finding to review.\n\n'
           '[View full review in Proofline](https://proofline.sh/path)\n\n'
           '<!-- proofline:published-findings --> <!-- proofline:published-finding-series=abc -->\n'
           '<!-- /proofline:published-findings -->')
    assert blocks(review_text(raw)[2]) == '<p><strong>Proofline</strong> has 1 new finding to review.</p>'


def test_review_disclosure_formats_details_and_leaves_code_literals_alone():
    raw = ('<details>\n<summary>Details</summary>\n\nFollow [`reports.py`](https://example.test/file).\n\n'
           '```html\n<!-- proofline:literal -->\n<details>\n```\n</details>')
    assert blocks(raw) == (
        '<details class="review-disclosure"><summary>Reviewer details</summary><div class="disclosure-copy">'
        '<p>Follow <code>reports.py</code>.</p><pre><code>&lt;!-- proofline:literal --&gt;\n&lt;details&gt;</code></pre>'
        '</div></details>'
    )
    assert '&lt;details onclick=' in blocks('<details onclick="untrusted">')
