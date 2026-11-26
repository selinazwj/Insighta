"""Features / quality / settings."""

from app.models import Survey


def _parse_survey_auto_filter_from_form(form) -> tuple:
    """Read quality auto-filter defaults from publish/edit form."""
    from app.features.quality.reviews import _parse_auto_approve_min_score

    enabled_raw = form.get("quality_auto_filter_enabled")
    if isinstance(enabled_raw, list):
        enabled_raw = enabled_raw[-1] if enabled_raw else "0"
    enabled = str(enabled_raw or "").strip().lower() in {"1", "on", "true", "yes"}
    min_score = _parse_auto_approve_min_score(form.get("quality_auto_filter_min_score", 80))
    return enabled, min_score


def _apply_survey_auto_filter_settings(survey: Survey, form) -> None:
    enabled, min_score = _parse_survey_auto_filter_from_form(form)
    survey.quality_auto_filter_enabled = enabled
    survey.quality_auto_filter_min_score = min_score


__all__ = ['_parse_survey_auto_filter_from_form', '_apply_survey_auto_filter_settings']
