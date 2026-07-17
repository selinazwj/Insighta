#!/usr/bin/env python3
"""Static SEO regression audit for Insighta.

The audit is intentionally dependency-free so it can run before installing the
application. It checks the reusable SEO layer, index-control defaults, public
page semantics, structured-data plumbing, local image references, deployment
configuration, and accidental credential/documentation regressions.

Usage:
    python scripts/seo_audit.py
    python scripts/seo_audit.py --strict
    python scripts/seo_audit.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "app" / "templates"
STATIC = ROOT / "app" / "static"
MAIN = ROOT / "api" / "main.py"
SEO_MODULE = ROOT / "app" / "seo.py"
SEO_PARTIAL = TEMPLATES / "_seo_head.html"

PUBLIC_TEMPLATES = {
    "index.html",
    "participant_landing.html",
    "studies.html",
    "recruitment_share.html",
    "about.html",
    "privacy.html",
    "terms.html",
}

EXPECTED_PUBLIC_ROUTES = {
    "/",
    "/participant",
    "/studies",
    "/studies/{category_slug}",
    "/r/{share_slug}",
    "/about",
    "/privacy",
    "/terms",
    "/robots.txt",
    "/sitemap.xml",
}

EXPECTED_SEO_ENV = {
    "SEO_SITE_URL",
    "SEO_SITE_NAME",
    "SEO_CONTACT_EMAIL",
    "SEO_DEFAULT_IMAGE",
    "SEO_LANGUAGE",
    "SEO_LOCALE",
    "SEO_INDEX_STUDIES",
    "GOOGLE_SITE_VERIFICATION",
    "BING_SITE_VERIFICATION",
}


@dataclass(frozen=True)
class Finding:
    level: str
    check: str
    detail: str


class Audit:
    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def pass_(self, check: str, detail: str) -> None:
        self.findings.append(Finding("PASS", check, detail))

    def fail(self, check: str, detail: str) -> None:
        self.findings.append(Finding("FAIL", check, detail))

    def warn(self, check: str, detail: str) -> None:
        self.findings.append(Finding("WARN", check, detail))

    def require(self, condition: bool, check: str, ok: str, bad: str) -> None:
        (self.pass_ if condition else self.fail)(check, ok if condition else bad)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def route_literals(source: str) -> set[str]:
    return set(re.findall(r'@app\.(?:get|post|put|patch|delete)\(\s*["\']([^"\']+)', source))


def iter_img_tags(html: str) -> Iterable[str]:
    return re.findall(r"<img\b[^>]*>", html, flags=re.IGNORECASE | re.DOTALL)


def audit_templates(audit: Audit) -> None:
    templates = sorted(TEMPLATES.glob("*.html"))
    missing_partial = [p.name for p in templates if p.name != SEO_PARTIAL.name and '{% include "_seo_head.html" %}' not in read(p)]
    audit.require(
        not missing_partial,
        "Template index controls",
        f"All {len(templates) - 1} page templates include the shared SEO partial.",
        "Missing shared SEO partial: " + ", ".join(missing_partial),
    )

    for name in sorted(PUBLIC_TEMPLATES):
        path = TEMPLATES / name
        if not path.exists():
            audit.fail("Public template inventory", f"Missing {name}.")
            continue
        html = read(path)
        h1_count = len(re.findall(r"<h1\b", html, flags=re.IGNORECASE))
        audit.require(
            h1_count == 1,
            f"{name}: primary heading",
            "Exactly one H1 is present.",
            f"Expected one H1, found {h1_count}.",
        )
        audit.require(
            bool(re.search(r"<title>\s*{{\s*seo\.title\s*}}\s*</title>", html)),
            f"{name}: dynamic title",
            "The document title is sourced from the route SEO payload.",
            "The public page title is not sourced from seo.title.",
        )
        audit.require(
            '<html lang="en">' in html,
            f"{name}: language",
            "The document language is declared.",
            "Missing an explicit English lang attribute.",
        )
        audit.require(
            'name="viewport"' in html,
            f"{name}: viewport",
            "A responsive viewport is declared.",
            "Missing viewport metadata.",
        )

        bad_images: list[str] = []
        for tag in iter_img_tags(html):
            alt = re.search(r"\balt\s*=\s*([\"'])(.*?)\1", tag, flags=re.IGNORECASE | re.DOTALL)
            if not alt or not alt.group(2).strip():
                bad_images.append(re.sub(r"\s+", " ", tag)[:160])
        audit.require(
            not bad_images,
            f"{name}: image alternatives",
            "Every image has a non-empty alt attribute.",
            "Images without useful alt text: " + " | ".join(bad_images),
        )

        audit.require(
            'href="#"' not in html,
            f"{name}: crawlable links",
            "No public navigation link uses a placeholder # destination.",
            "A public link still uses href=\"#\".",
        )


def audit_seo_layer(audit: Audit) -> None:
    main = read(MAIN)
    seo = read(SEO_MODULE)
    partial = read(SEO_PARTIAL)

    routes = route_literals(main)
    missing_routes = sorted(EXPECTED_PUBLIC_ROUTES - routes)
    audit.require(
        not missing_routes,
        "Public route inventory",
        "All expected public SEO routes are registered.",
        "Missing public routes: " + ", ".join(missing_routes),
    )

    required_helpers = {
        "home_seo",
        "participant_seo",
        "studies_directory_seo",
        "category_seo",
        "study_seo",
        "content_page_seo",
        "organization_schema",
        "breadcrumb_schema",
    }
    missing_helpers = [name for name in sorted(required_helpers) if not re.search(rf"def\s+{re.escape(name)}\s*\(", seo)]
    audit.require(
        not missing_helpers,
        "Reusable SEO API",
        "Metadata and schema builders exist for all public page types.",
        "Missing SEO helpers: " + ", ".join(missing_helpers),
    )

    controls = {
        "canonical": 'rel="canonical"' in partial,
        "description": 'name="description"' in partial,
        "robots": 'name="robots"' in partial and "noindex, nofollow, noarchive" in partial,
        "Open Graph": 'property="og:title"' in partial and 'property="og:image"' in partial,
        "Twitter card": 'name="twitter:card"' in partial,
        "JSON-LD": 'type="application/ld+json"' in partial,
        "verification": "google-site-verification" in partial and "msvalidate.01" in partial,
    }
    missing_controls = [name for name, present in controls.items() if not present]
    audit.require(
        not missing_controls,
        "Shared head metadata",
        "Canonical, snippets, robots, social cards, JSON-LD, and verification tags are centralized.",
        "Missing shared metadata controls: " + ", ".join(missing_controls),
    )

    audit.require(
        '"@context": "https://schema.org"' in seo,
        "Schema.org context",
        "Every generated JSON-LD block receives the schema.org context.",
        "Structured data is missing a schema.org @context wrapper.",
    )
    audit.require(
        "html_safe_json" in seo and r"\\u003c" in seo and r"\\u003e" in seo,
        "JSON-LD output safety",
        "User-authored study text is escaped before insertion into JSON-LD script blocks.",
        "JSON-LD serialization does not visibly protect script termination characters.",
    )
    audit.require(
        'response.status_code >= 400' in main and 'X-Robots-Tag' in main,
        "HTTP index controls",
        "Private and error responses receive X-Robots-Tag protection.",
        "Missing HTTP-level noindex protection for private/error responses.",
    )
    audit.require(
        'survey.status != "published"' in main and 'noindex, follow, noarchive' in main,
        "Closed study handling",
        "Closed study pages remain usable but are explicitly excluded from indexing.",
        "Closed study pages do not have an explicit noindex response policy.",
    )
    audit.require(
        "Serve one canonical, responsive homepage" in main,
        "Mobile-first consistency",
        "Desktop and mobile user agents receive the same canonical homepage route.",
        "The canonical responsive homepage policy is not present.",
    )
    audit.require(
        '@app.get("/robots.txt"' in main and 'Sitemap:' in main,
        "robots.txt generation",
        "robots.txt is generated dynamically and advertises the sitemap.",
        "robots.txt or its Sitemap directive is missing.",
    )
    audit.require(
        '@app.get("/sitemap.xml"' in main and 'entries[:50000]' in main,
        "XML sitemap generation",
        "The sitemap includes static and published dynamic pages with the protocol entry limit.",
        "Dynamic sitemap generation or its entry limit is missing.",
    )


def audit_assets_and_config(audit: Audit) -> None:
    seo = read(SEO_MODULE)
    local_paths = sorted(set(re.findall(r'["\'](/static/[^"\']+)["\']', seo)))
    missing_assets = [path for path in local_paths if not (STATIC / path.removeprefix("/static/")).exists()]
    audit.require(
        not missing_assets,
        "SEO image assets",
        f"All {len(local_paths)} locally referenced SEO images exist.",
        "Missing local SEO assets: " + ", ".join(missing_assets),
    )

    env_path = ROOT / ".env.example"
    env_text = read(env_path) if env_path.exists() else ""
    configured = set(re.findall(r"^([A-Z][A-Z0-9_]*)=", env_text, flags=re.MULTILINE))
    missing_env = sorted(EXPECTED_SEO_ENV - configured)
    audit.require(
        not missing_env,
        "SEO deployment variables",
        "All supported SEO environment variables are documented in .env.example.",
        "Undocumented SEO environment variables: " + ", ".join(missing_env),
    )

    manifest_path = STATIC / "manifest.json"
    try:
        manifest = json.loads(read(manifest_path))
        icons_ok = bool(manifest.get("icons")) and all((STATIC / item["src"].replace("/static/", "")).exists() for item in manifest["icons"])
        manifest_ok = manifest.get("name") == "Insighta" and manifest.get("start_url") == "/" and icons_ok
    except (OSError, KeyError, json.JSONDecodeError):
        manifest_ok = False
    audit.require(
        manifest_ok,
        "Web app manifest",
        "The manifest is valid and its declared icons exist.",
        "The manifest is invalid or references missing icons.",
    )


def audit_content_and_secrets(audit: Audit) -> None:
    index = read(TEMPLATES / "index.html")
    unsupported_markers = [
        "100+",
        "94%",
        "Higher than industry average",
        "no fees, no delays",
        "no hidden fees",
        "no minimums",
    ]
    present = [marker for marker in unsupported_markers if marker.lower() in index.lower()]
    audit.require(
        not present,
        "Unsupported marketing claims",
        "The public homepage does not contain the previously hard-coded numerical/performance claims.",
        "Potentially unsupported homepage claims remain: " + ", ".join(present),
    )

    docs = [ROOT / "README.md", ROOT / "Insighta_README_current_v2.md", ROOT / ".env.example"]
    exposed: list[str] = []
    for path in docs:
        if not path.exists():
            continue
        for line_no, line in enumerate(read(path).splitlines(), start=1):
            match = re.match(r"\s*(?:EMAIL_PASSWORD|STRIPE_SECRET_KEY|GOOGLE_CLIENT_SECRET|LINKEDIN_CLIENT_SECRET|ANTHROPIC_API_KEY)\s*=\s*(.+?)\s*$", line)
            if match:
                value = match.group(1).strip()
                if value and not any(token in value.lower() for token in ("your-", "example", "placeholder", "<", "sk-ant-your", "changeme")):
                    exposed.append(f"{path.name}:{line_no}")
    audit.require(
        not exposed,
        "Documented secret hygiene",
        "No populated secret values were found in tracked setup documentation.",
        "Potential populated secrets found at: " + ", ".join(exposed),
    )


def compile_sources(audit: Audit) -> None:
    failures: list[str] = []
    for path in (MAIN, SEO_MODULE, ROOT / "scripts" / "smoke_test_seo.py"):
        try:
            compile(read(path), str(path), "exec")
        except SyntaxError as exc:
            failures.append(f"{path.relative_to(ROOT)}:{exc.lineno}: {exc.msg}")
    audit.require(
        not failures,
        "Python syntax",
        "SEO source and smoke-test files compile successfully.",
        "Syntax failures: " + " | ".join(failures),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()

    audit = Audit()
    audit_templates(audit)
    audit_seo_layer(audit)
    audit_assets_and_config(audit)
    audit_content_and_secrets(audit)
    compile_sources(audit)

    failures = [f for f in audit.findings if f.level == "FAIL"]
    warnings = [f for f in audit.findings if f.level == "WARN"]
    passes = [f for f in audit.findings if f.level == "PASS"]

    if args.json:
        print(json.dumps({
            "summary": {"passed": len(passes), "warnings": len(warnings), "failed": len(failures)},
            "findings": [asdict(f) for f in audit.findings],
        }, indent=2, ensure_ascii=False))
    else:
        symbols = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]"}
        for finding in audit.findings:
            print(f"{symbols[finding.level]} {finding.check}: {finding.detail}")
        print(f"\nSEO audit: {len(passes)} passed, {len(warnings)} warnings, {len(failures)} failed.")

    return 1 if failures or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
