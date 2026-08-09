from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "AnalyticsPage.tsx"
ANALYTICS_TEST = REPO_ROOT / "frontend" / "src" / "pages" / "AnalyticsPage.test.tsx"
CHART = REPO_ROOT / "frontend" / "src" / "components" / "AccessibleLineChart.tsx"
CHART_TEST = REPO_ROOT / "frontend" / "src" / "components" / "AccessibleLineChart.test.tsx"


@pytest.mark.safety("SAFE-25")
def test_analytics_page_keeps_no_causation_contract_under_ui_test() -> None:
    page = ANALYTICS_PAGE.read_text(encoding="utf-8")
    test = ANALYTICS_TEST.read_text(encoding="utf-8")
    chart = CHART.read_text(encoding="utf-8")
    chart_test = CHART_TEST.read_text(encoding="utf-8")
    assert "Association does not establish causation" in page
    assert "Association does not establish causation" in test
    assert "Association does not establish causation" in chart
    assert "Association does not establish causation" in chart_test


@pytest.mark.safety("SAFE-26")
def test_analytics_page_keeps_missing_distinct_from_zero_under_ui_test() -> None:
    page = ANALYTICS_PAGE.read_text(encoding="utf-8")
    test = ANALYTICS_TEST.read_text(encoding="utf-8")
    for label in ("Missing—no dose facts", "Missing—no approved plan"):
        assert label in page
        assert label in test
    assert "Gap—no value" in CHART.read_text(encoding="utf-8")
    assert "Gap—no value" in CHART_TEST.read_text(encoding="utf-8")
