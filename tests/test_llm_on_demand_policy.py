import inspect
from pathlib import Path

from app.quality_engine import compute_excel_row_quality, compute_quality_score, evaluate_builtin_response
from app.screening_engine import generate_screening_draft


ROOT = Path(__file__).resolve().parents[1]


def test_quality_llm_is_opt_in_by_default():
    assert inspect.signature(compute_quality_score).parameters["run_llm"].default is False
    assert inspect.signature(compute_excel_row_quality).parameters["run_llm"].default is False
    assert inspect.signature(evaluate_builtin_response).parameters["run_llm"].default is False


def test_screening_ai_is_opt_in_at_function_level():
    assert inspect.signature(generate_screening_draft).parameters["use_ai"].default is False


def test_dashboard_passive_ai_refresh_defaults_off():
    source = (ROOT / "api" / "main.py").read_text()
    assert 'os.environ.get("INSIGHTA_DASHBOARD_AI_AUTO_REFRESH", "0")' in source
    assert "if _dashboard_ai_auto_refresh_enabled():" in source


def test_publisher_pages_do_not_auto_fetch_forecasts():
    publisher = (ROOT / "app" / "templates" / "publisher.html").read_text()
    publisher_study = (ROOT / "app" / "templates" / "publisher_study.html").read_text()
    assert "loadAiForecasts();" not in publisher
    assert "Load forecast" in publisher
    assert "if (tab === 'ai') loadForecast(false);" not in publisher_study
    assert "Generate forecast" in publisher_study


def test_excel_ai_review_requires_explicit_checkbox():
    results = (ROOT / "app" / "templates" / "survey_results.html").read_text()
    main_source = (ROOT / "api" / "main.py").read_text()
    assert 'id="qualityUseLlm"' in results
    assert "data.append('use_llm'" in results
    assert "run_llm=bool(use_llm)" in main_source
