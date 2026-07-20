"""Reusable SEO metadata, structured-data, and public URL helpers for Insighta.

The module intentionally keeps SEO policy separate from route/business logic so new
public pages can opt into indexing explicitly. Pages without an ``seo`` context are
rendered with ``noindex`` by the shared Jinja head partial.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from html import unescape
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

SITE_NAME = (os.environ.get("SEO_SITE_NAME") or "Insighta").strip()
SITE_URL = (os.environ.get("SEO_SITE_URL") or os.environ.get("BASE_URL") or "https://insightaco.org").strip()
SITE_URL = SITE_URL.rstrip("/")
SITE_LOCALE = (os.environ.get("SEO_LOCALE") or "en_US").strip()
SITE_LANGUAGE = (os.environ.get("SEO_LANGUAGE") or "en-US").strip()
CONTACT_EMAIL = (os.environ.get("SEO_CONTACT_EMAIL") or os.environ.get("EMAIL_ADDRESS") or "insightacom@gmail.com").strip()
DEFAULT_IMAGE_PATH = (os.environ.get("SEO_DEFAULT_IMAGE") or "/static/screenshot-desktop.png").strip()
GOOGLE_SITE_VERIFICATION = (os.environ.get("GOOGLE_SITE_VERIFICATION") or "").strip()
BING_SITE_VERIFICATION = (os.environ.get("BING_SITE_VERIFICATION") or "").strip()
INDEX_PUBLIC_STUDIES = (os.environ.get("SEO_INDEX_STUDIES") or "true").strip().lower() in {"1", "true", "yes", "on"}

DEFAULT_DESCRIPTION = (
    "Insighta connects researchers with qualified participants for surveys and interviews, "
    "with study recruitment, matching, response tracking, and participant rewards in one platform."
)

CATEGORY_CONTENT: dict[str, dict[str, str]] = {
    "research": {
        "label": "General research",
        "heading": "General research studies",
        "title": "General Research Studies & Surveys | Insighta",
        "description": "Browse open research studies, surveys, and interviews across a range of topics on Insighta. Review eligibility, time, format, and reward before joining.",
        "intro": "Explore open studies across multiple research areas. Each listing explains who the study is for, what participation involves, the expected time, and any available reward.",
        "image": "/static/psych.jpg",
    },
    "academic": {
        "label": "Academic research",
        "heading": "Academic research studies",
        "title": "Academic Research Studies & Surveys | Insighta",
        "description": "Find academic research studies and surveys from students, faculty, and research teams. Compare eligibility, study format, time commitment, and rewards.",
        "intro": "Discover studies created for academic projects, theses, coursework, and faculty-led research. Read each listing carefully before deciding whether to participate.",
        "image": "/static/r2.jpg",
    },
    "life": {
        "label": "Lifestyle and wellbeing",
        "heading": "Lifestyle and wellbeing studies",
        "title": "Lifestyle & Wellbeing Research Studies | Insighta",
        "description": "Browse lifestyle and wellbeing research studies on habits, health, routines, and daily experiences. See eligibility, time, study format, and reward details.",
        "intro": "These studies explore everyday habits, wellbeing, routines, and lived experiences. Listings show the participation format and the audience the researcher hopes to reach.",
        "image": "/static/campus_life.jpg",
    },
    "market": {
        "label": "Consumer and market research",
        "heading": "Consumer and market research studies",
        "title": "Consumer & Market Research Studies | Insighta",
        "description": "Explore consumer and market research surveys, interviews, and product studies. Compare participation requirements, duration, format, and available rewards.",
        "intro": "Help research teams understand products, services, brands, and consumer behavior. Every listing includes key participation details before you create an account or start.",
        "image": "/static/habit.png",
    },
    "clubs": {
        "label": "Community and campus",
        "heading": "Community and campus studies",
        "title": "Community & Campus Research Studies | Insighta",
        "description": "Find community, campus, club, and student-organization research opportunities. Review eligibility, time, participation format, and reward details.",
        "intro": "Browse studies focused on communities, student groups, campus life, and organizations. Choose only the opportunities that fit your background and interests.",
        "image": "/static/fb.jpg",
    },
    "other": {
        "label": "Other studies",
        "heading": "Other research opportunities",
        "title": "Other Research Studies & Opportunities | Insighta",
        "description": "Browse additional research studies, surveys, and interviews on Insighta. Check the topic, eligibility criteria, time commitment, format, and reward before joining.",
        "intro": "Explore research opportunities that do not fit one of Insighta's main categories. Each listing provides the information needed to decide whether it is a suitable match.",
        "image": "/static/food.jpeg",
    },
}

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def site_url(path: str = "/") -> str:
    """Return a canonical absolute URL on the configured production origin."""
    if not path:
        path = "/"
    parsed = urlsplit(path)
    if parsed.scheme in {"http", "https"}:
        return _normalize_absolute_url(path)
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{SITE_URL}{path}"


def _normalize_absolute_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"}:
        return site_url(value)
    # Fragments never belong in canonical or social image URLs generated here.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, ""))


def absolute_image_url(value: str | None) -> str:
    candidate = (value or DEFAULT_IMAGE_PATH).strip()
    if candidate.startswith(("http://", "https://")):
        return _normalize_absolute_url(candidate)
    return site_url(candidate)


def plain_text(value: Any, max_length: int | None = None) -> str:
    """Convert potentially user-authored HTML-ish text to a compact safe summary."""
    text = unescape(_TAG_RE.sub(" ", str(value or "")))
    text = _SPACE_RE.sub(" ", text).strip()
    if max_length and len(text) > max_length:
        shortened = text[: max_length - 1].rsplit(" ", 1)[0].rstrip(" ,.;:-")
        text = f"{shortened or text[: max_length - 1].rstrip()}…"
    return text


def html_safe_json(value: Mapping[str, Any] | Sequence[Any]) -> str:
    """Serialize JSON-LD so user text cannot terminate its script element."""
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    return (
        payload.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def category_content(slug: str | None) -> dict[str, str]:
    normalized = (slug or "research").strip().lower()
    return CATEGORY_CONTENT.get(normalized, CATEGORY_CONTENT["other"])


def category_image(slug: str | None) -> str:
    return category_content(slug).get("image", DEFAULT_IMAGE_PATH)


def category_label(slug: str | None) -> str:
    return category_content(slug).get("label", "Research")


def _verification_fields() -> dict[str, str]:
    return {
        "google_site_verification": GOOGLE_SITE_VERIFICATION,
        "bing_site_verification": BING_SITE_VERIFICATION,
    }


def _seo_payload(
    *,
    title: str,
    description: str,
    canonical_path: str,
    robots: str = "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1",
    image: str | None = None,
    image_alt: str | None = None,
    og_type: str = "website",
    json_ld: Iterable[Mapping[str, Any]] = (),
    prev_path: str | None = None,
    next_path: str | None = None,
) -> dict[str, Any]:
    clean_title = plain_text(title, 70)
    clean_description = plain_text(description, 170) or DEFAULT_DESCRIPTION
    canonical = site_url(canonical_path)
    payload: dict[str, Any] = {
        "title": clean_title,
        "description": clean_description,
        "canonical": canonical,
        "robots": robots,
        "image": absolute_image_url(image),
        "image_alt": plain_text(image_alt or f"{SITE_NAME} research platform", 140),
        "og_type": og_type,
        "site_name": SITE_NAME,
        "locale": SITE_LOCALE,
        "language": SITE_LANGUAGE,
        "twitter_card": "summary_large_image",
        "json_ld": [
            html_safe_json(block if "@context" in block else {"@context": "https://schema.org", **block})
            for block in json_ld
        ],
        **_verification_fields(),
    }
    if prev_path:
        payload["prev"] = site_url(prev_path)
    if next_path:
        payload["next"] = site_url(next_path)
    return payload


def organization_schema() -> dict[str, Any]:
    organization: dict[str, Any] = {
        "@type": "Organization",
        "@id": site_url("/#organization"),
        "name": SITE_NAME,
        "url": site_url("/"),
        "logo": {
            "@type": "ImageObject",
            "url": site_url("/static/icon-512.png"),
            "contentUrl": site_url("/static/icon-512.png"),
            "width": 512,
            "height": 512,
        },
        "description": DEFAULT_DESCRIPTION,
    }
    if CONTACT_EMAIL:
        organization["email"] = CONTACT_EMAIL
        organization["contactPoint"] = {
            "@type": "ContactPoint",
            "email": CONTACT_EMAIL,
            "contactType": "customer support",
            "availableLanguage": ["English"],
        }
    social_urls = [url.strip() for url in (os.environ.get("SEO_SOCIAL_URLS") or "").split(",") if url.strip()]
    if social_urls:
        organization["sameAs"] = social_urls
    return organization


def website_schema() -> dict[str, Any]:
    return {
        "@type": "WebSite",
        "@id": site_url("/#website"),
        "url": site_url("/"),
        "name": SITE_NAME,
        "description": DEFAULT_DESCRIPTION,
        "publisher": {"@id": site_url("/#organization")},
        "inLanguage": SITE_LANGUAGE,
    }


def breadcrumb_schema(items: Sequence[tuple[str, str]]) -> dict[str, Any]:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": position,
                "name": plain_text(name, 100),
                "item": site_url(path),
            }
            for position, (name, path) in enumerate(items, start=1)
        ],
    }


def home_seo() -> dict[str, Any]:
    title = "Insighta | Recruit Research Participants & Join Studies"
    page = {
        "@type": "WebPage",
        "@id": site_url("/#webpage"),
        "url": site_url("/"),
        "name": title,
        "description": DEFAULT_DESCRIPTION,
        "isPartOf": {"@id": site_url("/#website")},
        "about": {"@id": site_url("/#organization")},
        "inLanguage": SITE_LANGUAGE,
    }
    return _seo_payload(
        title=title,
        description=DEFAULT_DESCRIPTION,
        canonical_path="/",
        image=DEFAULT_IMAGE_PATH,
        image_alt="Insighta platform for research recruitment and study participation",
        json_ld=(organization_schema(), website_schema(), page),
    )


def participant_seo() -> dict[str, Any]:
    title = "Research Studies for Participants | Insighta"
    description = (
        "Find surveys, interviews, and research studies that match your profile. "
        "Review eligibility, time commitment, format, and rewards before choosing whether to participate."
    )
    page = {
        "@type": "WebPage",
        "@id": site_url("/participant#webpage"),
        "url": site_url("/participant"),
        "name": title,
        "description": description,
        "isPartOf": {"@id": site_url("/#website")},
        "audience": {"@type": "Audience", "audienceType": "Research study participants"},
        "inLanguage": SITE_LANGUAGE,
    }
    return _seo_payload(
        title=title,
        description=description,
        canonical_path="/participant",
        image="/static/screenshot-mobile.png",
        image_alt="Insighta participant study dashboard",
        json_ld=(page, breadcrumb_schema((("Home", "/"), ("For participants", "/participant")))),
    )


def studies_directory_seo(studies: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    title = "Research Studies, Surveys & Interviews | Insighta"
    description = (
        "Browse open research studies, surveys, and interviews on Insighta. Compare eligibility, "
        "time commitment, study format, and reward details before participating."
    )
    item_list = _study_item_list(studies, site_url("/studies#study-list"))
    page = {
        "@type": "CollectionPage",
        "@id": site_url("/studies#webpage"),
        "url": site_url("/studies"),
        "name": title,
        "description": description,
        "isPartOf": {"@id": site_url("/#website")},
        "mainEntity": {"@id": site_url("/studies#study-list")},
        "inLanguage": SITE_LANGUAGE,
    }
    return _seo_payload(
        title=title,
        description=description,
        canonical_path="/studies",
        image=DEFAULT_IMAGE_PATH,
        image_alt="Open research studies on Insighta",
        json_ld=(page, item_list, breadcrumb_schema((("Home", "/"), ("Studies", "/studies")))),
    )


def category_seo(category_slug: str, studies: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    content = category_content(category_slug)
    path = f"/studies/{category_slug}"
    item_list = _study_item_list(studies, site_url(f"{path}#study-list"))
    page = {
        "@type": "CollectionPage",
        "@id": site_url(f"{path}#webpage"),
        "url": site_url(path),
        "name": content["title"],
        "description": content["description"],
        "isPartOf": {"@id": site_url("/#website")},
        "mainEntity": {"@id": site_url(f"{path}#study-list")},
        "inLanguage": SITE_LANGUAGE,
    }
    robots = (
        "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"
        if studies
        else "noindex, follow"
    )
    return _seo_payload(
        title=content["title"],
        description=content["description"],
        canonical_path=path,
        robots=robots,
        image=content.get("image"),
        image_alt=f"{content['heading']} on Insighta",
        json_ld=(
            page,
            item_list,
            breadcrumb_schema((("Home", "/"), ("Studies", "/studies"), (content["label"], path))),
        ),
    )


def _study_item_list(studies: Sequence[Mapping[str, Any]], schema_id: str) -> dict[str, Any]:
    elements = []
    for position, study in enumerate(studies, start=1):
        path = str(study.get("share_path") or "").strip()
        if not path:
            continue
        elements.append(
            {
                "@type": "ListItem",
                "position": position,
                "url": site_url(path),
                "name": plain_text(study.get("title"), 120),
            }
        )
    return {
        "@type": "ItemList",
        "@id": schema_id,
        "numberOfItems": len(elements),
        "itemListElement": elements,
    }


def study_seo(study: Any, share_path: str, *, indexable: bool = True) -> dict[str, Any]:
    title_text = plain_text(getattr(study, "title", "Research study"), 100) or "Research study"
    description_text = plain_text(getattr(study, "description", ""), 155)
    if not description_text:
        description_text = "Review this Insighta research study's eligibility, time commitment, format, and participation details."
    raw_category_slug = (getattr(study, "category", None) or "research").strip().lower()
    category_slug = raw_category_slug if raw_category_slug in CATEGORY_CONTENT else "other"
    category = category_content(category_slug)
    image = getattr(study, "image_url", None) or category.get("image")
    robots = (
        "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"
        if indexable and INDEX_PUBLIC_STUDIES
        else "noindex, follow, noarchive"
    )
    minutes = max(int(getattr(study, "estimated_time", 0) or 0), 0)
    published_at = getattr(study, "published_at", None) or getattr(study, "created_at", None)
    audience_parts = [
        getattr(study, "target_age_range", None),
        getattr(study, "target_state", None),
        getattr(study, "target_field", None),
        getattr(study, "target_status", None),
    ]
    audience = ", ".join(plain_text(value, 80) for value in audience_parts if plain_text(value, 80)) or "Eligible research participants"
    project: dict[str, Any] = {
        "@type": "ResearchProject",
        "@id": site_url(f"{share_path}#study"),
        "url": site_url(share_path),
        "name": title_text,
        "description": description_text,
        "image": absolute_image_url(image),
        "mainEntityOfPage": {"@id": site_url(f"{share_path}#webpage")},
        "audience": {"@type": "Audience", "audienceType": audience},
        "keywords": [
            keyword
            for keyword in (category["label"], "research study", plain_text(getattr(study, "task_type", None), 50))
            if keyword
        ],
        "inLanguage": SITE_LANGUAGE,
    }
    if minutes:
        project["timeRequired"] = f"PT{minutes}M"
    if published_at:
        project["datePublished"] = published_at
    page = {
        "@type": "WebPage",
        "@id": site_url(f"{share_path}#webpage"),
        "url": site_url(share_path),
        "name": f"{title_text} — Research Study | {SITE_NAME}",
        "description": description_text,
        "isPartOf": {"@id": site_url("/#website")},
        "mainEntity": {"@id": site_url(f"{share_path}#study")},
        "inLanguage": SITE_LANGUAGE,
    }
    return _seo_payload(
        title=f"{title_text} — Research Study | {SITE_NAME}",
        description=description_text,
        canonical_path=share_path,
        robots=robots,
        image=image,
        image_alt=f"{title_text} research study",
        og_type="article",
        json_ld=(
            page,
            project,
            breadcrumb_schema(
                (
                    ("Home", "/"),
                    ("Studies", "/studies"),
                    (category["label"], f"/studies/{category_slug}"),
                    (title_text, share_path),
                )
            ),
        ),
    )


def content_page_seo(
    *,
    title: str,
    description: str,
    path: str,
    page_type: str = "WebPage",
    image: str | None = None,
    breadcrumb_label: str | None = None,
    indexable: bool = True,
) -> dict[str, Any]:
    page = {
        "@type": page_type,
        "@id": site_url(f"{path}#webpage"),
        "url": site_url(path),
        "name": title,
        "description": description,
        "isPartOf": {"@id": site_url("/#website")},
        "inLanguage": SITE_LANGUAGE,
    }
    robots = (
        "index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1"
        if indexable
        else "noindex, follow"
    )
    return _seo_payload(
        title=title,
        description=description,
        canonical_path=path,
        robots=robots,
        image=image or DEFAULT_IMAGE_PATH,
        json_ld=(page, breadcrumb_schema((("Home", "/"), (breadcrumb_label or title, path)))),
    )
