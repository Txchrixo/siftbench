from reviewbench.report.comment import blocks, review_text


def test_review_metadata_becomes_a_readable_heading():
    greptile = ('<a href=""><img alt="P1" src="https://greptile-static-assets.s3.amazonaws.com/'
                'badges/p1.svg?v=9" align="top"></a> **History skips rows**\n\nUse a `cursor`.')
    proofline = ('<!-- proofline:review-comment -->\n<!-- proofline:finding-series=example -->\n'
                 '**Warning** · History skips rows\n\nUse a `cursor`.\n\n'
                 '[View finding in Proofline](https://proofline.sh/path)')
    assert review_text(greptile) == ("History skips rows", "P1", "Use a `cursor`.")
    assert review_text(proofline) == ("History skips rows", "Warning", "Use a `cursor`.")


def test_review_formatting_keeps_untrusted_content_inert():
    rendered = blocks('**Finding** on `db`.\n\n- <img src=x onerror=alert(1)>\n'
                      '- [Open](https://example.test/a)\n\n```html\n<script>run()</script>\n```')
    assert rendered == (
        '<p><strong>Finding</strong> on <code>db</code>.</p>'
        '<ul><li>&lt;img src=x onerror=alert(1)&gt;</li><li>Open</li></ul>'
        '<pre><code>&lt;script&gt;run()&lt;/script&gt;</code></pre>'
    )
