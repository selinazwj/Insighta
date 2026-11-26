"""Features / public / catalog."""

from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import Optional
from app.models import Survey
from app.seo import CATEGORY_CONTENT
from app.seo import INDEX_PUBLIC_STUDIES
from app.seo import category_image
from app.seo import category_label
from app.seo import plain_text


def _public_study_cards(db: Session, category_slug: Optional[str] = None) -> list[dict]:
    from app.features.studies.fields import _participant_study_type_label
    from app.features.studies.fields import _task_type_label
    from app.features.studies.sharing import _ensure_survey_share_slug

    if not INDEX_PUBLIC_STUDIES:
        return []
    query = db.query(Survey).filter(Survey.status == "published")
    if category_slug:
        query = query.filter(func.lower(Survey.category) == category_slug.lower())
    surveys = query.order_by(Survey.published_at.desc(), Survey.created_at.desc()).all()

    slugs_changed = False
    for survey in surveys:
        if not getattr(survey, "share_slug", None):
            _ensure_survey_share_slug(db, survey)
            slugs_changed = True
    if slugs_changed:
        db.commit()

    cards: list[dict] = []
    for survey in surveys:
        display_reward = getattr(survey, "admin_display_reward_amount", None)
        if display_reward is None:
            display_reward = survey.reward_amount
        raw_category = (survey.category or "other").strip().lower()
        normalized_category = raw_category if raw_category in CATEGORY_CONTENT else "other"
        cards.append({
            "id": survey.id,
            "title": survey.title,
            "summary": plain_text(survey.description, 180),
            "description": survey.description,
            "category": normalized_category,
            "category_label": category_label(normalized_category),
            "category_path": f"/studies/{normalized_category}",
            "share_path": f"/r/{survey.share_slug}",
            "image": survey.image_url or category_image(normalized_category),
            "estimated_time": survey.estimated_time,
            "display_reward": float(display_reward or 0),
            "task_type_label": _participant_study_type_label(survey) or _task_type_label(getattr(survey, "task_type", None)),
            "published_at": survey.published_at or survey.created_at,
        })
    return cards


def _category_navigation(cards: list[dict]) -> list[dict]:
    counts = {slug: 0 for slug in CATEGORY_CONTENT}
    for card in cards:
        slug = card.get("category") or "other"
        if slug not in counts:
            slug = "other"
        counts[slug] += 1
    return [
        {
            "slug": slug,
            "label": content["label"],
            "path": f"/studies/{slug}",
            "count": counts.get(slug, 0),
        }
        for slug, content in CATEGORY_CONTENT.items()
    ]


def _sitemap_lastmod(value: Optional[datetime]) -> Optional[str]:
    if not value:
        return None
    return value.date().isoformat()


__all__ = ['_public_study_cards', '_category_navigation', '_sitemap_lastmod']
