# LLM Token Optimization

This revision keeps the public response shapes and Claude-generated completion probabilities, while reducing repeated context, verbose model output, unnecessary calls, and oversized analysis payloads.

## Main changes

### AI Growth prediction and ranking

- Uses the existing Haiku model alias by default instead of Sonnet.
- Serializes compact JSON and removes empty/null fields before each request.
- Batch responses now ask for compact fields (`p`, `c`, reason codes) instead of repeated prose.
- Expands reason codes and recommendation copy locally without changing API response fields.
- Reduces the default candidate pool from 120 to 60.
- Extends prediction cache lifetime from 6 hours to 24 hours.
- Uses dynamic output limits based on batch size.
- Publisher summaries are local by default, avoiding a second LLM call after predictions are already available. Set `AI_GROWTH_LLM_SUMMARY_MODE=llm` to restore model-written summaries.
- Draft preview sends aggregate pool distributions plus six representative profiles instead of up to 80 full participant records.

### Quality engine

- `QUALITY_LLM_MODE=high_risk` runs semantic review only for ambiguous or suspicious responses.
- `balanced` restores broader triggering; `off` disables semantic LLM calls.
- Caps semantic context to a bounded set of questions and truncates long answers.
- Uses a compact JSON output schema and a much smaller output limit.

### Survey result analysis

- Sends local distributions, averages, and bounded text samples instead of every raw answer.
- Loads answers in one query and aggregates locally.
- Caches analyses for unchanged survey result sets.
- Reduces the default output limit from 1500 to 750 tokens.

### Channel discovery and authoring tools

- Channel discovery defaults to Haiku, returns at most five channels, halves web-search uses from 8 to 4, lowers output limits, and caches results for 12 hours.
- AI form fill and question generation now truncate user input, use shorter prompts, and use tighter dynamic output limits.

## Measured payload-size proxies

These numbers compare serialized request characters on the repository's offline fixture. Character count is not identical to billed tokens, but it is a useful directional proxy.

| Path | Before | After | Reduction |
|---|---:|---:|---:|
| Participant ranking, 2 candidates | 3,375 | 1,384 | 59.0% |
| Survey ranking, 2 surveys | 4,479 | 1,803 | 59.7% |
| Draft preview, 3 profiles | 3,839 | 1,628 | 57.6% |
| Quality semantic prompt, 20 long text answers | 20,034 | 5,496 | 72.6% |
| Survey analysis, 15 long text responses | 9,677 | 3,725 | 61.5% |

A representative 20-item prediction response shrank from 8,508 to 2,008 serialized characters by replacing repeated prose with reason codes, a 76.4% reduction before provider-side tokenization.

## Observability

Set `AI_GROWTH_LOG_TOKEN_USAGE=true` to log Anthropic-reported input and output token counts for the central prediction client.

## Validation

Run:

```bash
python scripts/smoke_test_llm_only.py
python scripts/smoke_test_token_optimization.py
```

Both suites are offline and do not make external model calls.
