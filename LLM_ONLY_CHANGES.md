# Insighta LLM-only prediction/recommendation changes

This package modifies the uploaded Insighta repository so completion prediction and recommendation ranking are handled by Claude through the Anthropic API.

## Changed files

- `app/ai_growth/llm.py` — new Claude API wrapper, prompts, sanitized payload helpers.
- `app/ai_growth/prediction.py` — fully replaced. The original weighted rule model is removed. Local code now only prepares context, calls Claude, validates JSON, caches predictions, and aggregates Claude outputs.
- `api/main.py` — participant desktop/mobile dashboard can consume cached Claude `completion_probability`, but passive cache misses no longer trigger Claude by default.
- `app/templates/publisher.html` / `publisher_study.html` — AI forecasts are now loaded only after an explicit user action.
- `app/quality_engine.py` — automatic quality scoring defaults to local rule/anomaly checks; LLM semantic review is opt-in per call.
- `.env.example` — shows where to set the Claude API key/model and keeps passive dashboard AI refresh disabled by default.

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
- `/dashboard` and `/dashboard/mobile` may use already-cached Claude completion probabilities, which costs no new tokens. Cache misses use local ranking and do **not** enqueue Claude work by default.
- Publisher AI forecast cards do not call Claude on page load; `Load forecast`, `Recompute`, and `Top candidates` are explicit triggers.
- Built-in response submission does not call Claude for quality scoring; local rule/anomaly scoring still runs.
- Excel quality imports are also local-only by default; AI semantic review requires the explicit opt-in checkbox.

## On-demand token policy

The default deployment is now **LLM on-demand**:

```bash
INSIGHTA_DASHBOARD_AI_AUTO_REFRESH=0
```

With that default, normal dashboard/page views and the 5-minute background-job cron do not create new recommendation LLM calls. Existing passive recommendation jobs from older deployments are safely marked done without invoking Claude. Set `INSIGHTA_DASHBOARD_AI_AUTO_REFRESH=1` only if you intentionally want the old passive background refresh behavior.

Importing the Anthropic SDK or constructing ordinary web pages does not itself consume tokens; tokens are spent when an Anthropic `messages.create(...)` request is actually made.

## No rule model

The old `rule-v0.1` weighted score has been removed from the prediction path. The only probability/recommendation model is Claude. If Claude is not configured or the API request fails, the app returns an explicit `llm_ok: false` response instead of silently falling back to a local score.
