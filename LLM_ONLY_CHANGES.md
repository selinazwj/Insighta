# Insighta LLM-only prediction/recommendation changes

This package modifies the uploaded Insighta repository so completion prediction and recommendation ranking are handled by Claude through the Anthropic API.

## Changed files

- `app/ai_growth/llm.py` — new Claude API wrapper, prompts, sanitized payload helpers.
- `app/ai_growth/prediction.py` — fully replaced. The original weighted rule model is removed. Local code now only prepares context, calls Claude, validates JSON, caches predictions, and aggregates Claude outputs.
- `api/main.py` — participant desktop/mobile dashboard sorting now uses Claude `completion_probability` via `recommend_surveys_for_user(...)`, not urgency/date scoring.
- `.env.example` — shows where to set the Claude API key and model name.

## Required environment variables

```bash
ANTHROPIC_API_KEY=sk-ant-your-key-here
AI_GROWTH_CLAUDE_MODEL=claude-sonnet-4-5
```

`requirements.txt` already includes `anthropic`.

## Behavior

- `/api/surveys/{survey_id}/prediction/me` calls Claude for one participant/survey pair.
- `/api/surveys/{survey_id}/prediction/respondents` batches candidates through Claude and sorts by Claude probability.
- `/api/surveys/{survey_id}/prediction/summary` aggregates Claude predictions and asks Claude for publisher-facing advice.
- `/api/prediction/preview` uses Claude for unsaved survey draft preview.
- `/dashboard` and `/dashboard/mobile` rank surveys by Claude completion probability.

## No rule model

The old `rule-v0.1` weighted score has been removed from the prediction path. The only probability/recommendation model is Claude. If Claude is not configured or the API request fails, the app returns an explicit `llm_ok: false` response instead of silently falling back to a local score.
