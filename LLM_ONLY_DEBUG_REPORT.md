# LLM-only debug report

This repository was checked after converting the AI Growth prediction/recommendation path to Claude LLM-only.

## What was tested

- Full Python syntax compilation for `app`, `api`, and `scripts`.
- Import checks for:
  - `app.ai_growth.llm`
  - `app.ai_growth.prediction`
  - `api.main`
- FastAPI route loading and `/openapi.json` generation.
- Offline Claude wrapper behavior when `ANTHROPIC_API_KEY` is missing.
- Offline mocked-Claude tests for:
  - single user/survey prediction
  - prediction cache save/read
  - batch respondent prediction
  - survey recommendation ranking for dashboard ordering
  - publisher summary generation
  - unsaved survey preview generation
- FastAPI dashboard and mobile dashboard rendering with mocked Claude recommendations.

## Notes

The smoke tests do not call the real Anthropic API and do not require an API key. They monkeypatch Claude responses so the application flow can be tested offline.

Run:

```bash
python scripts/smoke_test_llm_only.py
```

To use real Claude calls, set:

```bash
ANTHROPIC_API_KEY=sk-ant-...
AI_GROWTH_CLAUDE_MODEL=claude-haiku-4-5-20251001
```

If `ANTHROPIC_API_KEY` is not set, the app returns explicit `llm_ok: false` results instead of falling back to any local scoring model.
