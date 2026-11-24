from pathlib import Path
from runpy import run_path


def test_pilot_bug_is_reproducible_before_any_reviewer_run():
    root = Path(__file__).resolve().parents[1] / "benchmark" / "pilot" / "pagination"

    def pages(version):
        module = run_path(str(root / version / "reports.py"))
        report, next_page = module["Report"], module["next_page"]
        data = [report(41, 100, "Orders"), report(42, 100, "Returns"), report(43, 102, "Stock")]
        first, cursor = next_page(data, size=1)
        second, _ = next_page(data, size=1, after=cursor)
        return [item.report_id for item in first + second]

    assert pages("base") == [41, 42]
    assert pages("proposed") == [41, 43]
