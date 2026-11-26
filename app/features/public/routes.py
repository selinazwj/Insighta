"""Features / public / routes."""

from datetime import datetime
from fastapi import Cookie
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import PlainTextResponse
from fastapi.responses import RedirectResponse
from fastapi.responses import Response as HTTPResponse
from sqlalchemy.orm import Session
from typing import Optional
from xml.sax.saxutils import escape as xml_escape
import os
from app.bootstrap.runtime import templates
from app.database import get_db
from app.features.auth.dependencies import get_current_user
from app.models import User
from app.pricing import pricing_config
from app.seo import CATEGORY_CONTENT
from app.seo import category_content
from app.seo import category_seo
from app.seo import content_page_seo
from app.seo import home_seo
from app.seo import participant_seo
from app.seo import site_url as seo_site_url
from app.seo import studies_directory_seo


def robots_txt():
    if os.environ.get("INSIGHTA_ENV") == "staging":
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    # Private HTML pages remain crawlable long enough for their noindex directives to be seen.
    body = "\n".join([
        "User-agent: *",
        "Allow: /",
        "Disallow: /api/",
        "Disallow: /admin/",
        "Disallow: /auth/",
        "Disallow: /connect/",
        "Disallow: /webhook/",
        "Disallow: /static/uploads/",
        "",
        f"Sitemap: {seo_site_url('/sitemap.xml')}",
        "",
    ])
    return PlainTextResponse(
        body,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def sitemap_xml(db: Session = Depends(get_db)):
    from app.features.public.catalog import _public_study_cards
    from app.features.public.catalog import _sitemap_lastmod

    entries: list[tuple[str, Optional[str]]] = [
        (seo_site_url("/"), None),
        (seo_site_url("/participant"), None),
        (seo_site_url("/studies"), None),
        (seo_site_url("/about"), None),
        (seo_site_url("/how-it-works"), None),
        (seo_site_url("/privacy"), None),
        (seo_site_url("/terms"), None),
        (seo_site_url("/pricing"), None),
    ]
    cards = _public_study_cards(db)
    category_lastmods: dict[str, Optional[datetime]] = {}
    for card in cards:
        slug = card["category"] if card["category"] in CATEGORY_CONTENT else "other"
        published_at = card.get("published_at")
        current = category_lastmods.get(slug)
        if published_at and (current is None or published_at > current):
            category_lastmods[slug] = published_at
        entries.append((seo_site_url(card["share_path"]), _sitemap_lastmod(published_at)))
    for slug, lastmod in category_lastmods.items():
        entries.append((seo_site_url(f"/studies/{slug}"), _sitemap_lastmod(lastmod)))

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for loc, lastmod in entries[:50000]:
        lines.append("  <url>")
        lines.append(f"    <loc>{xml_escape(loc)}</loc>")
        if lastmod:
            lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return HTTPResponse(
        content="\n".join(lines),
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=300, stale-while-revalidate=3600"},
    )


def public_studies(request: Request, db: Session = Depends(get_db)):
    from app.features.public.catalog import _category_navigation
    from app.features.public.catalog import _public_study_cards

    cards = _public_study_cards(db)
    return templates.TemplateResponse("studies.html", {
        "request": request,
        "surveys": cards,
        "categories": _category_navigation(cards),
        "total_studies": len(cards),
        "active_category": None,
        "page_heading": "Open research studies",
        "page_intro": "Browse surveys, interviews, and other research opportunities. Review the audience, format, estimated time, and reward before deciding whether to participate.",
        "seo": studies_directory_seo(cards),
    })


def public_studies_by_category(category_slug: str, request: Request, db: Session = Depends(get_db)):
    from app.features.public.catalog import _category_navigation
    from app.features.public.catalog import _public_study_cards

    normalized = category_slug.strip().lower()
    if normalized not in CATEGORY_CONTENT:
        raise HTTPException(404, "Study category not found")
    all_cards = _public_study_cards(db)
    cards = [card for card in all_cards if card.get("category") == normalized]
    content = category_content(normalized)
    response = templates.TemplateResponse("studies.html", {
        "request": request,
        "surveys": cards,
        "categories": _category_navigation(all_cards),
        "total_studies": len(all_cards),
        "active_category": normalized,
        "page_heading": content["heading"],
        "page_intro": content["intro"],
        "seo": category_seo(normalized, cards),
    })
    if not cards:
        response.headers["X-Robots-Tag"] = "noindex, follow"
    return response


def how_it_works_page(request: Request):
    seo = content_page_seo(
        title="How Insighta works | Research Recruitment & Participation",
        description="See how researchers create and fund studies, and how participants find opportunities, complete eligibility checks and track rewards.",
        path="/how-it-works", breadcrumb_label="How it works",
    )
    return templates.TemplateResponse("how_it_works.html", {"request":request, "seo":seo})


def about_page(request: Request):
    seo = content_page_seo(
        title="About Insighta | Research Recruitment Platform",
        description="Learn how Insighta helps researchers recruit qualified participants and helps people discover surveys, interviews, and studies that fit their background.",
        path="/about",
        page_type="AboutPage",
        breadcrumb_label="About",
    )
    return templates.TemplateResponse("about.html", {"request": request, "seo": seo})


def privacy_page(request: Request):
    seo = content_page_seo(
        title="Privacy | Insighta",
        description="Read how Insighta handles account, profile, study, payment, and technical information used to operate the research participation platform.",
        path="/privacy",
        breadcrumb_label="Privacy",
    )
    return templates.TemplateResponse("privacy.html", {"request": request, "seo": seo})


def pricing_page(request: Request):
    seo = content_page_seo(title="Research recruitment pricing | Insighta",
        description="See recruitment rates, per-person minimum fees, and urgency charges for Insighta studies.",
        path="/pricing", breadcrumb_label="Pricing")
    return templates.TemplateResponse("pricing.html", {"request": request, "seo": seo, "pricing": pricing_config()})


def terms_page(request: Request):
    seo = content_page_seo(
        title="Terms of Use | Insighta",
        description="Review the core rules for researchers and participants using Insighta to publish, discover, complete, and manage research studies.",
        path="/terms",
        breadcrumb_label="Terms of use",
    )
    return templates.TemplateResponse("terms.html", {"request": request, "seo": seo})


def index(request: Request):
    # Serve one canonical, responsive homepage to desktop and mobile crawlers alike.
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "show": None, "error": None, "seo": home_seo()}
    )


def participant_app_entry(request: Request, user_id: str = Cookie(None)):
    from app.features.auth.cookies import _mark_participant_app
    from app.features.auth.navigation import _participant_dashboard_url
    from app.web.middleware import no_store_response

    if user_id:
        response = RedirectResponse(_participant_dashboard_url(request), status_code=302)
    else:
        response = no_store_response(
            templates.TemplateResponse(
                "participant_landing.html",
                {"request": request, "seo": participant_seo()}
            )
        )
    _mark_participant_app(response, request)
    return response


def participant_login_entry(request: Request):
    from app.features.auth.cookies import _mark_participant_app

    response = RedirectResponse("/login?role=participant", status_code=302)
    _mark_participant_app(response, request)
    return response


def choice(request: Request, user_id: str = Cookie(None), db: Session = Depends(get_db)):
    from app.features.auth.navigation import _is_mobile_request
    from app.features.auth.navigation import _participant_dashboard_url
    from app.web.middleware import no_store_response

    if _is_mobile_request(request):
        target_url = _participant_dashboard_url(request) if user_id else "/participant"
    else:
        target_url = "/publisher" if user_id else "/login?role=researcher"
    return no_store_response(RedirectResponse(target_url, status_code=303))


def guide_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("guide.html", {"request": request, "current_user": current_user})


__all__ = ['robots_txt', 'sitemap_xml', 'public_studies', 'public_studies_by_category', 'how_it_works_page', 'about_page', 'privacy_page', 'pricing_page', 'terms_page', 'index', 'participant_app_entry', 'participant_login_entry', 'choice', 'guide_page']
