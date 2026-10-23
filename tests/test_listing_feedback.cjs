const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const publisher = fs.readFileSync(path.join(root, 'app/templates/publish_external.html'), 'utf8');
const builder = fs.readFileSync(path.join(root, 'app/templates/survey_builder.html'), 'utf8');

function element(value = '') {
  const classes = new Set();
  return {
    value, textContent: '', innerHTML: '', style: {}, dataset: {}, hidden: false,
    classList: { add: v => classes.add(v), remove: v => classes.delete(v), contains: v => classes.has(v),
      toggle(v, enabled) { if (enabled) classes.add(v); else classes.delete(v); } },
    listeners: {}, addEventListener(name, callback) { this.listeners[name] = callback; },
    querySelectorAll() { return []; }, contains() { return false; },
    setAttribute() {}, removeAttribute() {}, closest() { return null; }, focus() {},
  };
}

test('dropping a poster attaches the first file and reports invalid images', () => {
  const elements = Object.fromEntries(['posterInput', 'fileZone', 'fzName', 'posterError'].map(id => [id, element()]));
  elements.posterInput.files = [];
  class Transfer {
    constructor() {
      this.files = [];
      this.items = { add: file => this.files.push(file) };
    }
  }
  const context = vm.createContext({
    document: { getElementById: id => elements[id] },
    window: { addEventListener() {} }, DataTransfer: Transfer,
  });
  vm.runInContext(publisher.split('// ── FILE UPLOAD ──')[1].split('// ── AI FILL ──')[0], context);
  let prevented = false;
  const image = { name: 'poster.png', type: 'image/png' };
  elements.fileZone.listeners.dragover({ preventDefault() { prevented = true; }, dataTransfer: {} });
  assert.equal(prevented, true);
  assert.equal(elements.fileZone.classList.contains('drag-over'), true);
  elements.fileZone.listeners.drop({ preventDefault() {}, dataTransfer: { files: [image, { name: 'extra.png' }] } });
  assert.equal(elements.posterInput.files.length, 1);
  assert.equal(elements.posterInput.files[0], image);
  assert.equal(elements.fzName.textContent, 'poster.png');
  assert.equal(elements.fileZone.classList.contains('drag-over'), false);
  elements.fileZone.listeners.drop({ preventDefault() {}, dataTransfer: { files: [{ name: 'notes.txt', type: 'text/plain' }] } });
  assert.equal(elements.posterInput.value, '');
  assert.equal(elements.posterError.hidden, false);
});

test('draft saves reuse the same entry and show the central confirmation only once', () => {
  const stored = new Map();
  const elements = Object.fromEntries(['draftStatus', 'bottomDraftStatus', 'draftConfirmation'].map(id => [id, element()]));
  let confirmations = 0;
  elements.draftConfirmation.showModal = () => confirmations++;
  const title = { name: 'title', type: 'text', value: 'My study' };
  const context = vm.createContext({
    DRAFT_KEY: 'test-draft', DRAFT_NOTICE_KEY: 'test-notice',
    document: { querySelectorAll: () => [title], getElementById: id => elements[id] },
    localStorage: { getItem: key => stored.get(key), setItem: (key, value) => stored.set(key, value) },
    setTimeout() {}, clearTimeout() {},
  });
  vm.runInContext(publisher.split('// ── LOCAL DRAFT ──')[1].split('// ── VALIDATION ──')[0], context);
  assert.equal(vm.runInContext('saveDraft()', context), true);
  title.value = 'Updated study';
  assert.equal(vm.runInContext('saveDraft()', context), true);
  assert.equal(confirmations, 1);
  assert.equal(stored.size, 2);
  assert.equal(JSON.parse(stored.get('test-draft')).data.title, 'Updated study');
  assert.match(elements.bottomDraftStatus.textContent, /Saved on this device/);
  context.localStorage.setItem = () => { throw new Error('Storage denied'); };
  assert.equal(vm.runInContext('saveDraft()', context), false);
  assert.match(elements.draftStatus.textContent, /Could not save/);
});

async function loadBuilder(failSaves = false) {
  const elements = new Map();
  const getElementById = id => {
    if (!elements.has(id)) elements.set(id, element(id === 'infoTitle' ? 'Survey title' : ''));
    return elements.get(id);
  };
  const timers = new Map();
  let timerId = 0;
  const writes = [], alerts = [];
  const context = vm.createContext({
    document: { getElementById },
    window: { location: { href: '' } },
    setTimeout(callback) { const id = ++timerId; timers.set(id, callback); return id; },
    clearTimeout(id) { timers.delete(id); },
    alert(message) { alerts.push(message); }, URLSearchParams,
    async fetch(url, options) {
      if (!options?.method) return { ok: true, json: async () => [
        { id: 1, question_text: 'First', question_type: 'text' },
        { id: 2, question_text: 'Second', question_type: 'text' },
      ] };
      writes.push({ url, body: JSON.parse(options.body) });
      return { ok: !failSaves };
    },
  });
  const script = builder.match(/<script>\s*([\s\S]*?)<\/script>/)[1].replace('{{ survey_id }}', '1');
  await vm.runInContext(script, context);
  return { context, timers, writes, alerts, elements };
}

test('editing two questions keeps both pending saves and flushes before publishing', async () => {
  const { context, timers, writes } = await loadBuilder();
  const html = vm.runInContext("document.getElementById('qList').innerHTML", context);
  const handlers = [...html.matchAll(/oninput="([^"]+)"/g)].map(match => match[1]);
  ['Edited first', 'Edited second'].forEach((text, index) => {
    vm.runInContext(`(function(){${handlers[index]}}).call({value:${JSON.stringify(text)},style:{},scrollHeight:40})`, context);
  });
  assert.equal(timers.size, 2);
  await vm.runInContext('finishAndPublish()', context);
  assert.equal(timers.size, 0);
  assert.deepEqual(writes.filter(w => w.url.includes('/questions/')).map(w => w.body.question_text), ['Edited first', 'Edited second']);
  assert.match(context.window.location.href, /^\/publish\?/);
});

test('a failed autosave never displays Saved or navigates to publishing', async () => {
  const { context, writes, alerts, elements } = await loadBuilder(true);
  await vm.runInContext('finishAndPublish()', context);
  assert.equal(writes.length, 3);
  assert.equal(context.window.location.href, '');
  assert.match(elements.get('saveStatus').textContent, /Could not save/);
  assert.match(alerts[0], /could not be saved/);
});
