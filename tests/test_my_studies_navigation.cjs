const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const template = fs.readFileSync(path.join(__dirname, '../app/templates/participant_my_studies.html'), 'utf8');

test('every My Studies title and primary study action opens the landing page without starting it', () => {
  const surveys = [
    { id: 1, is_started: true, link: 'https://example.com/form' },
    { id: 2, is_started: true, link: '' },
    { id: 3, is_skipped: true, link: 'https://example.com/form' },
    { id: 4, is_completed: true },
    { id: 5, is_live_study: true, status: 'scheduled', link: 'https://example.com/meeting' },
    { id: 6, is_live_study: true, is_completed: true },
    { id: 7, is_live_study: true, status: 'scheduling', calendar_url: '/bookings/7/calendar.ics' },
  ].map(s => ({ ...s, title: 'Research & eligibility', share_path: `/r/study-${s.id}` }));
  const elements = new Map();
  const context = vm.createContext({
    document: { getElementById(id) { if (!elements.has(id)) elements.set(id, {}); return elements.get(id); } },
    fetch() { assert.fail('Viewing study details must not change participation state'); },
    window: { open() { assert.fail('Continue must stay in the current tab'); } },
  });
  const script = template.match(/<script>([\s\S]*?)<\/script>/)[1]
    .replace('{{ surveys | tojson | safe }}', JSON.stringify(surveys));
  vm.runInContext(script, context);
  const html = elements.get('studyList').innerHTML;
  for (const s of surveys) {
    const row = html.split(`id="row-${s.id}"`)[1].split('class="study-row')[0];
    const links = [...row.matchAll(/href="(\/r\/[^"]+)"/g)].map(m => m[1]);
    assert.deepEqual(links, [`/r/study-${s.id}?from=my-studies`, `/r/study-${s.id}?from=my-studies`]);
    assert.match(row, /Research &amp; eligibility/);
    assert.doesNotMatch(row, /doTake\(/);
  }
  assert.match(html, /Continue\s*<\/a>/);
  assert.match(html, /Take study\s*<\/a>/);
  assert.match(html, /Open meeting link/);
  assert.match(html, /\/bookings\/7\/calendar\.ics/);
});
