#!/usr/bin/env python3
"""
Install Insighta AI Growth implementation into the current repository.

Usage from repository root:
  python scripts/install_ai_growth.py

It copies app/ai_growth/*, patches api/main.py, and lightly enhances the
existing dashboard/publisher/login templates. Backups are created before every
modified file.
"""

from __future__ import annotations

import argparse
import re
import sys
import shutil
from datetime import datetime
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")


def _looks_like_repo(path: Path) -> bool:
    return (path / "api" / "main.py").exists() and (path / "app" / "models.py").exists()


def detect_repo_root(cli_root: str | None = None) -> Path:
    """Find the Insighta repository root even when this package is nested.

    Supported layouts:
    1. repo/scripts/install_ai_growth.py and repo/app/ai_growth already present.
    2. repo/insighta_ai_full_impl_debug/scripts/install_ai_growth.py copied as a nested package.
    3. Running from the repository root with python path/to/install_ai_growth.py.
    """
    candidates: list[Path] = []
    if cli_root:
        candidates.append(Path(cli_root).expanduser().resolve())
    candidates.extend([
        Path.cwd().resolve(),
        PKG_ROOT.resolve(),
        PKG_ROOT.parent.resolve(),
    ])
    candidates.extend(Path.cwd().resolve().parents)
    candidates.extend(PKG_ROOT.resolve().parents)

    seen = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        if _looks_like_repo(item):
            return item
    raise SystemExit(
        "Cannot locate Insighta repository root. Run from the repo root or pass --repo-root /path/to/Insighta."
    )


def _read_cli_repo_root() -> str | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--repo-root", default=None)
    args, _ = parser.parse_known_args()
    return args.repo_root


ROOT = detect_repo_root(_read_cli_repo_root())


def backup(path: Path) -> None:
    if path.exists():
        dst = path.with_suffix(path.suffix + f".before_ai_growth_{STAMP}")
        shutil.copy2(path, dst)
        print(f"[BACKUP] {path} -> {dst.name}")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    print(f"[WRITE] {path}")


def copy_ai_growth_package() -> None:
    src = PKG_ROOT / "app" / "ai_growth"
    dst = ROOT / "app" / "ai_growth"
    if not src.exists():
        raise SystemExit(f"Cannot find source package: {src}")

    # If the package has already been unpacked directly into the repository, src
    # and dst are identical. Do not delete the source while trying to install it.
    if src.resolve() == dst.resolve():
        print(f"[SKIP] app/ai_growth already in repository: {dst}")
        return

    if dst.exists():
        backup_marker = ROOT / f"app_ai_growth_backup_{STAMP}"
        shutil.copytree(dst, backup_marker)
        shutil.rmtree(dst)
        print(f"[BACKUP] existing app/ai_growth -> {backup_marker}")
    shutil.copytree(src, dst)
    print(f"[COPY] {src} -> {dst}")


def patch_main() -> None:
    path = ROOT / "api" / "main.py"
    backup(path)
    text = read(path)

    if "from app.ai_growth.routes import router as ai_growth_router" not in text:
        marker = "from app.verification.routes import router as verification_router"
        if marker not in text:
            raise SystemExit("Cannot find verification router import in api/main.py")
        text = text.replace(marker, marker + "\nfrom app.ai_growth.routes import router as ai_growth_router")

    if "from app.ai_growth.security import is_safe_internal_next" not in text:
        text = text.replace(
            "from app.ai_growth.routes import router as ai_growth_router",
            "from app.ai_growth.routes import router as ai_growth_router\nfrom app.ai_growth.security import is_safe_internal_next",
        )

    if "from app.ai_growth.jump import mark_latest_jump_completed_for_response" not in text:
        text = text.replace(
            "from app.ai_growth.security import is_safe_internal_next",
            "from app.ai_growth.security import is_safe_internal_next\nfrom app.ai_growth.jump import mark_latest_jump_completed_for_response",
        )

    if "app.include_router(ai_growth_router)" not in text:
        marker = "app.include_router(verification_router)"
        if marker not in text:
            raise SystemExit("Cannot find app.include_router(verification_router) in api/main.py")
        text = text.replace(marker, marker + "\napp.include_router(ai_growth_router)")

    # Login GET: add next query parameter and template variable.
    text = re.sub(
        r"def login_page\(\s*request: Request,\s*success: Optional\[str\] = None,\s*reset_success: Optional\[str\] = None,\s*email: Optional\[str\] = None,\s*oauth_error: Optional\[str\] = None,\s*\):",
        "def login_page(\n    request: Request,\n    success: Optional[str] = None,\n    reset_success: Optional[str] = None,\n    email: Optional[str] = None,\n    oauth_error: Optional[str] = None,\n    next: Optional[str] = None,\n):",
        text,
        flags=re.S,
    )
    if '"login_next": next if is_safe_internal_next(next) else "",' not in text:
        text = text.replace(
            '"reset_email": _normalize_email(email or ""),',
            '"reset_email": _normalize_email(email or ""),\n        "login_next": next if is_safe_internal_next(next) else "",',
        )

    # Login POST: accept next form field and redirect safely after successful login.
    text = re.sub(
        r"def login\(\s*request: Request,\s*email: str = Form\(\.\.\.\),\s*password: str = Form\(\.\.\.\),\s*db: Session = Depends\(get_db\)\s*\):",
        "def login(\n    request: Request,\n    email: str = Form(...),\n    password: str = Form(...),\n    next: Optional[str] = Form(None),\n    db: Session = Depends(get_db)\n):",
        text,
        flags=re.S,
    )
    if "redirect_target = next if is_safe_internal_next(next) else \"/choice\"" not in text:
        text = text.replace(
            'response = RedirectResponse("/choice", status_code=303)',
            'redirect_target = next if is_safe_internal_next(next) else "/choice"\n    response = RedirectResponse(redirect_target, status_code=303)',
            1,
        )

    # Built-in submit path: mark latest JumpEvent completed when built-in answer submission completes.
    if "mark_latest_jump_completed_for_response(db, r)" not in text:
        text = text.replace(
            'db.commit() return JSONResponse({"message": "submitted successfully"})',
            'db.commit()\n    mark_latest_jump_completed_for_response(db, r)\n    return JSONResponse({"message": "submitted successfully"})',
        )
        # For normally formatted files.
        text = text.replace(
            'db.commit()\n    return JSONResponse({"message": "submitted successfully"})',
            'db.commit()\n    mark_latest_jump_completed_for_response(db, r)\n    return JSONResponse({"message": "submitted successfully"})',
        )

    write(path, text)


def patch_dashboard() -> None:
    path = ROOT / "app" / "templates" / "dashboard.html"
    if not path.exists():
        print("[SKIP] dashboard.html not found")
        return
    backup(path)
    text = read(path)

    if ".ai-match-hint" not in text:
        text = text.replace(
            "</style>",
            """
.ai-match-hint { margin-top: 10px; padding: 8px 10px; border-radius: 14px; background: rgba(155,106,61,.08); border: 1px solid rgba(155,106,61,.18); color: var(--ink-muted); font-size: 12px; line-height: 1.4; }
.ai-match-hint strong { color: var(--accent); }
""" + "\n</style>",
        )

    if 'id="ai-hint-${s.id}"' not in text:
        text = text.replace(
            "<p>${s.desc}</p>",
            "<p>${s.desc}</p>\n  <div class=\"ai-match-hint\" id=\"ai-hint-${s.id}\">AI recommendation is loading...</div>",
        )

    # Replace startSurvey to use jump gateway for built-in/external/interview.
    new_start = r"""
  // Start task through Insighta jump gateway so Response and JumpEvent are always tracked.
  async function startSurvey(e, id, link, isBuiltIn) {
    e.preventDefault(); e.stopPropagation();
    try {
      const res = await fetch(`/api/surveys/${id}/jump/start?source=dashboard`, {
        method: 'POST',
        credentials: 'same-origin'
      });
      if (!res.ok) { alert(await res.text()); return; }
      const data = await res.json();
      if (data.destination_type === 'external' || data.destination_type === 'interview') {
        window.open(data.redirect_url, '_blank', 'noopener');
      } else {
        window.location.href = data.redirect_url;
      }
    } catch (err) {
      console.error(err);
      alert('Network error');
    }
  }
"""
    text = re.sub(
        r"\s*// Start survey — built-in goes to /take, external opens new tab\s*async function startSurvey\(e, id, link, isBuiltIn\) \{.*?\n\s*\}\s*\n\s*async function completeSurvey",
        "\n" + new_start + "\n  async function completeSurvey",
        text,
        flags=re.S,
    )

    if "async function hydrateAiCards" not in text:
        hydrate = r"""
  async function hydrateAiCards(container) {
    const hintEls = container.querySelectorAll('.ai-match-hint[id^="ai-hint-"]');
    hintEls.forEach(async (el) => {
      const id = el.id.replace('ai-hint-', '');
      try {
        await fetch('/api/activity/impression', {
          method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ survey_id: Number(id), source: 'dashboard' })
        });
        const res = await fetch(`/api/surveys/${id}/prediction/me`, { credentials: 'same-origin' });
        if (!res.ok) { el.textContent = 'AI recommendation unavailable'; return; }
        const data = await res.json();
        const pct = Math.round((data.completion_probability || 0) * 100);
        const reason = (data.top_reasons || [])[0] || 'Profile and task signals matched';
        el.innerHTML = `<strong>AI ${pct}% completion fit</strong> · ${reason}`;
      } catch (err) {
        el.textContent = 'AI recommendation unavailable';
      }
    });
  }
"""
        text = text.replace("  // Start", hydrate + "\n  // Start")

    if "hydrateAiCards(container);" not in text:
        text = text.replace("  }).join('');", "  }).join('');\n  hydrateAiCards(container);", 1)

    text = text.replace("Take Survey →", "Start & Jump →")
    text = text.replace("Book Interview →", "Book Interview →")

    write(path, text)


def patch_publisher() -> None:
    path = ROOT / "app" / "templates" / "publisher.html"
    if not path.exists():
        print("[SKIP] publisher.html not found")
        return
    backup(path)
    text = read(path)

    if ".ai-forecast-card" not in text:
        text = text.replace(
            "</style>",
            """
.ai-forecast-card { margin: 14px 0 16px; padding: 14px 16px; border-radius: 18px; background: linear-gradient(135deg, rgba(155,106,61,.10), rgba(124,58,237,.07)); border: 1px solid rgba(155,106,61,.18); }
.ai-forecast-title { font-weight: 800; color: var(--ink); margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
.ai-forecast-body { color: var(--ink-muted); font-size: 13px; line-height: 1.55; }
.ai-forecast-body strong { color: var(--accent); }
.ai-forecast-actions { margin-top: 10px; display:flex; gap:8px; flex-wrap:wrap; }
.ai-small-btn { border: 1px solid rgba(155,106,61,.25); background: #fff; border-radius: 999px; padding: 7px 11px; font-size: 12px; font-weight: 700; cursor: pointer; color: var(--accent); }
""" + "\n</style>",
        )

    card = """
  <div class="ai-forecast-card" data-survey-id="{{ s.id }}">
    <div class="ai-forecast-title">AI Completion Forecast</div>
    <div class="ai-forecast-body">Loading candidate completion forecast...</div>
    <div class="ai-forecast-actions">
      <button type="button" class="ai-small-btn" onclick="refreshForecast({{ s.id }})">Recompute</button>
      <button type="button" class="ai-small-btn" onclick="showTopRespondents({{ s.id }})">Top candidates</button>
    </div>
  </div>
"""
    if 'data-survey-id="{{ s.id }}"' not in text:
        # Insert after each progress row and before card actions.
        text = text.replace("  <div class=\"card-actions\">", card + "\n  <div class=\"card-actions\">")

    if "async function loadAiForecasts" not in text:
        script = r"""
  async function renderForecastCard(card, force=false) {
    const id = card.dataset.surveyId;
    const body = card.querySelector('.ai-forecast-body');
    body.textContent = 'Loading candidate completion forecast...';
    try {
      const res = await fetch(`/api/surveys/${id}/prediction/summary${force ? '?force=true' : ''}`, { credentials: 'same-origin' });
      if (!res.ok) { body.textContent = 'AI forecast unavailable.'; return; }
      const data = await res.json();
      const pct = Math.round((data.completion_probability || 0) * 100);
      const top = data.segment_label || 'General respondents';
      const risk = (data.risk_reasons || [])[0] || 'No major risk detected';
      const action = data.recommended_action || 'Keep current settings.';
      body.innerHTML = `<strong>${pct}% expected completion</strong> · ${data.candidate_count || 0} candidates · Top group: <strong>${top}</strong><br>Risk: ${risk}<br>Suggested action: ${action}`;
    } catch (err) {
      body.textContent = 'AI forecast unavailable.';
    }
  }

  async function loadAiForecasts() {
    document.querySelectorAll('.ai-forecast-card').forEach(card => renderForecastCard(card));
  }

  async function refreshForecast(id) {
    const card = document.querySelector(`.ai-forecast-card[data-survey-id="${id}"]`);
    if (card) renderForecastCard(card, true);
  }

  async function showTopRespondents(id) {
    try {
      const res = await fetch(`/api/surveys/${id}/prediction/respondents?limit=8`, { credentials: 'same-origin' });
      if (!res.ok) { alert(await res.text()); return; }
      const data = await res.json();
      const lines = (data.respondents || []).map((r, i) => `${i+1}. ${Math.round((r.completion_probability || 0)*100)}% · ${r.segment_label || 'Respondent'} · ${(r.top_reasons || [])[0] || ''}`);
      alert(lines.join('\n') || 'No candidate predictions yet.');
    } catch (err) {
      alert('AI candidate list unavailable.');
    }
  }

  loadAiForecasts();
"""
        text = text.replace("  // Notifications", script + "\n  // Notifications")

    write(path, text)


def patch_login_template() -> None:
    path = ROOT / "app" / "templates" / "login.html"
    if not path.exists():
        print("[SKIP] login.html not found")
        return
    backup(path)
    text = read(path)
    if 'name="next"' not in text:
        # Add hidden next field inside the first POST login form.
        text = re.sub(
            r"(<form[^>]+method=[\"']post[\"'][^>]*>)",
            r"\1\n  <input type=\"hidden\" name=\"next\" value=\"{{ login_next or '' }}\">",
            text,
            count=1,
            flags=re.I,
        )
    write(path, text)


def main() -> None:
    print(f"[INFO] package root: {PKG_ROOT}")
    print(f"[INFO] repo root: {ROOT}")
    required = [ROOT / "api" / "main.py", ROOT / "app" / "models.py", ROOT / "app" / "templates"]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise SystemExit("Run this script from the Insighta repository root. Missing: " + ", ".join(missing))
    copy_ai_growth_package()
    patch_main()
    patch_dashboard()
    patch_publisher()
    patch_login_template()
    print("\n[DONE] AI Growth implementation installed.")
    print("Run: python -m py_compile api/main.py app/ai_growth/*.py")
    print("Then start the app normally, e.g. uvicorn api.main:app --reload")


if __name__ == "__main__":
    main()
