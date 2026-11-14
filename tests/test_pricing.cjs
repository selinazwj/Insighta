const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/pricing.js', 'utf8');

function harness(fetch) {
  const timers = new Map();
  let id = 0;
  const context = { window: {}, Intl, AbortController, fetch,
    setTimeout: callback => { timers.set(++id, callback); return id; },
    clearTimeout: key => timers.delete(key) };
  vm.runInNewContext(source, context);
  const options = { box: {style: {}}, total: {value: ''}, status: {textContent: ''},
    values: Object.fromEntries(['reward', 'fee', 'urgency', 'total'].map(key => [key, {textContent: ''}])),
    input: () => ({study_type: 'in_person', per_person: '40', target_responses: '20'}) };
  return {options, update: context.window.InsightaPricing(options),
    run: () => { const callbacks = [...timers.values()]; timers.clear(); return callbacks.at(-1)(); }};
}
const quote = { reward_per_person_cents: 4000, recruitment_fee_per_person_cents: 1500,
  urgency_amount_cents: 11000, total_cents: 121000, rate_bps: 2500, minimum_fee_cents: 1500, minimum_applied: true };
const response = data => ({ok: true, json: async () => data});

test('renders backend cents without recomputing fees', async () => {
  let sent;
  const h = harness(async (url, options) => { sent = JSON.parse(options.body); return response(quote); });
  h.update(); await h.run();
  assert.equal(sent.per_person, '40');
  assert.equal(h.options.values.reward.textContent, '$40.00');
  assert.equal(h.options.values.fee.textContent, '$15.00');
  assert.equal(h.options.values.urgency.textContent, '$110.00');
  assert.equal(h.options.values.total.textContent, '$1,210.00');
  assert.equal(h.options.total.value, '1210.00');
});

test('a stale reply cannot overwrite the current quote', async () => {
  const pending = [];
  const h = harness(() => new Promise(resolve => pending.push(resolve)));
  h.update(); const old = h.run();
  h.update(); const current = h.run();
  pending[1](response(quote)); await current;
  pending[0](response({...quote, total_cents: 1})); await old;
  assert.equal(h.options.values.total.textContent, '$1,210.00');
});

test('network and validation errors remove stale totals', async () => {
  const h = harness(async () => ({ok: false, json: async () => ({detail: 'Enter a positive reward.'})}));
  h.options.total.value = '1210.00';
  h.update(); await h.run();
  assert.equal(h.options.total.value, '');
  assert.equal(h.options.values.total.textContent, '—');
  assert.equal(h.options.status.textContent, 'Enter a positive reward.');
});
