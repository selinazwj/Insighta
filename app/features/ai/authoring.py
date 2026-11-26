"""Features / ai / authoring."""

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import JSONResponse
import asyncio
import os
import re
from app.bootstrap.runtime import logger
from app.features.auth.dependencies import get_current_user
from app.models import User


def _fallback_ai_fill(prompt: str, warning: str | None = None) -> dict:
    clean = re.sub(r"\s+", " ", (prompt or "").strip())
    title_words = re.findall(r"[A-Za-z0-9]+", clean)[:8]
    title = " ".join(title_words).strip() or "Research Study"
    if len(title) > 64:
        title = title[:61].rstrip() + "..."
    lower = clean.lower()
    category = "research"
    if any(word in lower for word in ["class", "student", "school", "academic", "college", "university"]):
        category = "academic"
    elif any(word in lower for word in ["food", "habit", "shopping", "consumer", "market", "brand"]):
        category = "market"
    elif any(word in lower for word in ["club", "organization", "community"]):
        category = "clubs"
    elif any(word in lower for word in ["lifestyle", "sleep", "wellness", "fitness", "health"]):
        category = "life"
    numbers = [int(n) for n in re.findall(r"\b\d+\b", lower)]
    target_responses = next((n for n in numbers if 1 <= n <= 10000), 50)
    estimated_time = 10
    time_match = re.search(r"(\d+)\s*(?:min|minute|minutes)", lower)
    if time_match:
        estimated_time = max(1, min(120, int(time_match.group(1))))
    per_person = 5.0
    amount_match = re.search(r"\$\s*(\d+(?:\.\d+)?)", lower)
    if not amount_match:
        amount_match = re.search(r"(?:pay|reward|incentive|compensation)\s*(?:is|of|:)?\s*(\d+(?:\.\d+)?)", lower)
    if amount_match:
        per_person = max(0.0, min(500.0, float(amount_match.group(1))))
    result = {
        "title": title,
        "description": clean or "Help us validate this research study by completing a short survey. Your responses will be used for product and recruitment testing.",
        "category": category,
        "estimated_time": estimated_time,
        "per_person_gross": per_person,
        "target_responses": target_responses,
    }
    if warning:
        result["warning"] = warning
    return result


def _parse_ai_fill_json(text: str) -> dict:
    import json as _json
    match = re.search(r'\{.*\}', text or "", re.DOTALL)
    if not match:
        raise ValueError("AI response parsing failed")
    data = _json.loads(match.group())
    return {
        "title": str(data.get("title") or "").strip()[:80] or "Research Study",
        "description": str(data.get("description") or "").strip() or "Help us validate this research study by completing a short survey.",
        "category": data.get("category") if data.get("category") in {"research", "life", "clubs", "market", "academic", "other"} else "research",
        "estimated_time": int(float(data.get("estimated_time") or 10)),
        "per_person_gross": float(data.get("per_person_gross") or 5.0),
        "target_responses": int(float(data.get("target_responses") or 50)),
    }


async def _anthropic_ai_fill(prompt: str) -> dict:
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not configured")
    import anthropic
    configured_model = (os.environ.get("ANTHROPIC_AI_FILL_MODEL") or "").strip()
    allow_expensive_models = (os.environ.get("ALLOW_EXPENSIVE_ANTHROPIC_MODELS") or "").strip().lower() == "true"
    if configured_model and any(name in configured_model.lower() for name in ("opus", "fable")) and not allow_expensive_models:
        raise RuntimeError("ANTHROPIC_AI_FILL_MODEL points to an expensive model. Use Haiku or set ALLOW_EXPENSIVE_ANTHROPIC_MODELS=true explicitly.")
    model_candidates = [
        configured_model,
        "claude-haiku-4-5-20251001",
        "claude-haiku-4-5",
        "claude-3-5-haiku-20241022",
    ]
    model_candidates = [m for i, m in enumerate(model_candidates) if m and m not in model_candidates[:i]]
    client = anthropic.Anthropic(api_key=api_key, timeout=45.0, max_retries=0)
    prompt_text = f"""You are helping a researcher fill out a survey publishing form.
Based on this description: "{prompt}"
Return ONLY a valid JSON object with these exact fields, no extra text:
{{"title": "clear survey title under 10 words", "description": "2-3 sentence description", "category": "one of: research, life, clubs, market, academic, other", "estimated_time": 5, "per_person_gross": 5.00, "target_responses": 100}}"""
    errors = []
    for model in model_candidates:
        try:
            message = await asyncio.to_thread(client.messages.create,
                model=model, max_tokens=450,
                messages=[{"role": "user", "content": prompt_text}]
            )
            result = _parse_ai_fill_json(message.content[0].text)
            result["provider"] = f"anthropic:{model}"
            return result
        except Exception as exc:
            if not isinstance(exc, anthropic.NotFoundError):
                raise
            errors.append(f"{model}: {exc}")
    raise RuntimeError(" | ".join(errors))


async def _ai_fill_from_prompt(prompt: str) -> JSONResponse:
    if not prompt:
        raise HTTPException(400, "Prompt is required")
    try:
        return JSONResponse(await _anthropic_ai_fill(prompt))
    except Exception as exc:
        logger.warning("AI form fill unavailable: %s", type(exc).__name__)
        return JSONResponse(_fallback_ai_fill(prompt, "AI is temporarily unavailable. Basic fields were filled from your description; please review them."))


async def ai_fill(request: Request, current_user: User = Depends(get_current_user)):
    try:
        body = await request.json()
        return await _ai_fill_from_prompt(body.get("prompt", ""))
    except Exception as e:
        import traceback; print(traceback.format_exc())
        raise HTTPException(500, str(e))


async def ai_generate_questions(
    request: Request,
    current_user: User = Depends(get_current_user)
):
    try:
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            raise HTTPException(400, "Prompt is required")

        import anthropic, json as _json
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), timeout=45.0, max_retries=0)
        message = await asyncio.to_thread(client.messages.create,
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{
                "role": "user",
                "content": f"""You are helping a researcher design a survey.
Based on this research goal: "{prompt}"

Generate 6-8 survey questions. Return ONLY a valid JSON array, no extra text:
[
  {{
    "question_text": "What is your current year in school?",
    "question_type": "single",
    "options": ["Freshman", "Sophomore", "Junior", "Senior", "Graduate"],
    "is_required": true,
    "order_index": 1
  }},
  {{
    "question_text": "How many hours do you sleep on average per night?",
    "question_type": "scale",
    "options": null,
    "is_required": true,
    "order_index": 2
  }},
  {{
    "question_text": "Describe any sleep difficulties you experience.",
    "question_type": "text",
    "options": null,
    "is_required": false,
    "order_index": 3
  }}
]

Rules:
- Mix question types: single/multiple for closed, scale for ratings, text for open-ended
- Keep questions neutral, avoid leading language
- scale and text questions must have options: null
- options field is only for single, multiple, dropdown types
"""
            }]
        )

        text = message.content[0].text
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            raise HTTPException(500, "AI response parsing failed")

        questions = _json.loads(match.group())
        return JSONResponse({"questions": questions})

    except HTTPException:
        raise
    except Exception:
        logger.exception("Question generation failed")
        raise HTTPException(503, "Questions could not be generated right now. Please try again or add them manually.")


__all__ = ['_fallback_ai_fill', '_parse_ai_fill_json', '_anthropic_ai_fill', '_ai_fill_from_prompt', 'ai_fill', 'ai_generate_questions']
