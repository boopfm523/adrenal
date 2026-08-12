from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ANALYTICS_PAGE = REPO_ROOT / "frontend" / "src" / "pages" / "AnalyticsPage.tsx"
ANALYTICS_TEST = REPO_ROOT / "frontend" / "src" / "pages" / "AnalyticsPage.test.tsx"
DAILY_CURVE = REPO_ROOT / "frontend" / "src" / "components" / "DailyHealthCurve.tsx"
DAILY_CURVE_TEST = REPO_ROOT / "frontend" / "src" / "components" / "DailyHealthCurve.test.tsx"
CHART = REPO_ROOT / "frontend" / "src" / "components" / "AccessibleLineChart.tsx"
CHART_TEST = REPO_ROOT / "frontend" / "src" / "components" / "AccessibleLineChart.test.tsx"
CONTEXT_SETTINGS = REPO_ROOT / "frontend" / "src" / "components" / "ContextSettings.tsx"
CONTEXT_SETTINGS_TEST = REPO_ROOT / "frontend" / "src" / "pages" / "SettingsPage.test.tsx"
STYLES = REPO_ROOT / "frontend" / "src" / "styles.css"
HEALTH_DATA = REPO_ROOT / "frontend" / "src" / "pages" / "HealthDataPage.tsx"
HEALTH_DATA_TEST = REPO_ROOT / "frontend" / "src" / "pages" / "HealthDataPage.test.tsx"
PAGINATION = REPO_ROOT / "frontend" / "src" / "components" / "PaginationControls.tsx"
TIMELINE_TEST = REPO_ROOT / "frontend" / "src" / "pages" / "TimelinePage.test.tsx"


@pytest.mark.safety("SAFE-25")
def test_analytics_page_keeps_no_causation_contract_under_ui_test() -> None:
    curve = DAILY_CURVE.read_text(encoding="utf-8")
    curve_test = DAILY_CURVE_TEST.read_text(encoding="utf-8")
    test = ANALYTICS_TEST.read_text(encoding="utf-8")
    chart = CHART.read_text(encoding="utf-8")
    chart_test = CHART_TEST.read_text(encoding="utf-8")
    assert "Association does not establish causation" in curve
    assert "Association does not establish causation" in curve_test
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


def test_context_privacy_and_responsive_contracts_stay_under_ui_test() -> None:
    component = CONTEXT_SETTINGS.read_text(encoding="utf-8")
    test = CONTEXT_SETTINGS_TEST.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")
    for contract in (
        "Default: coarse location",
        "I consent to storing exact coordinates",
        "Weather not recorded—not zero",
        "Delete this context record",
    ):
        assert contract in component
        assert contract in test
    mobile = styles[styles.index("@media (max-width: 720px)") :]
    for responsive_grid in (
        ".context-entry-form",
        ".coordinate-fields",
        ".weather-fields",
        ".context-delete-form",
    ):
        assert responsive_grid in mobile


def test_vitals_accessibility_safety_and_responsive_contracts_stay_under_ui_test() -> None:
    page = HEALTH_DATA.read_text(encoding="utf-8")
    test = HEALTH_DATA_TEST.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")
    for contract in (
        "does not diagnose or recommend treatment",
        "absence of a record is not a zero",
        "Correction reason",
        "Source:",
    ):
        assert contract in page
    assert "View data table" in CHART.read_text(encoding="utf-8")
    for tested_semantic in (
        'getByRole("form", { name: "Record blood pressure"',
        'getByRole("columnheader", { name: "Systolic (mmHg)"',
        'getAllByRole("columnheader", { name: "Weight (lb)"',
        'getByRole("region", { name: "Weight records table"',
        'findByRole("img", { name: /Blood pressure/',
        "Revision history (1)",
    ):
        assert tested_semantic in test
    for responsive_table_contract in (
        'className="table-scroll vital-table-region" tabIndex={0}',
        '<table className="vital-table">',
        ".table-scroll { overflow-x: auto; }",
        ".vital-table { min-width: 58rem;",
    ):
        assert responsive_table_contract in page or responsive_table_contract in styles
    mobile = styles[styles.index("@media (max-width: 720px)") :]
    assert ".vital-entry-grid" in mobile
    assert ".vital-entry-form" in mobile


@pytest.mark.safety("SAFE-25")
def test_chart_axes_accessibility_and_phone_readability_stay_under_ui_test() -> None:
    chart = CHART.read_text(encoding="utf-8")
    test = CHART_TEST.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")
    for semantic in (
        "chart-axis-description",
        "chart-x-tick",
        "chart-y-tick",
        "Graph not plotted:",
    ):
        assert semantic in chart
    for tested_behavior in (
        "X axis:",
        "scrollable graph",
        "data table",
        "different units",
        "Gap—no value",
    ):
        assert tested_behavior in test
    for visual_contract in (
        ".chart-plot-scroll { max-width: 100%; overflow-x: auto;",
        ".line-chart { display: block; width: 100%; min-width: 38rem;",
        ".chart-x-tick text, .chart-y-tick text { fill: #26322d; font-size: 12px;",
    ):
        assert visual_contract in styles


def test_pagination_accessibility_and_phone_layout_stay_under_ui_test() -> None:
    component = PAGINATION.read_text(encoding="utf-8")
    timeline_test = TIMELINE_TEST.read_text(encoding="utf-8")
    styles = STYLES.read_text(encoding="utf-8")
    for semantic in ('aria-live="polite"', 'role="status"', "Previous", "Next"):
        assert semantic in component
    for tested_semantic in ('getByRole("status")', "Previous", "Next"):
        assert tested_semantic in timeline_test
    mobile = styles[styles.index("@media (max-width: 720px)") :]
    assert ".pagination { align-items: stretch; flex-direction: column; }" in mobile
    assert ".pagination__actions button { flex: 1; }" in mobile
