// Dependency-free DOM fixture: compact disclosure, one decision, freeze and copy.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.style = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener(type, fn) { this.events[type] = fn; }
  focus() {}
  querySelectorAll(tag) {
    return this.children.flatMap(c => c instanceof Element ? [c, ...c.querySelectorAll('*')] : [])
      .filter(c => tag === '*' || c.tag === tag);
  }
  querySelector(selector) {
    return this.querySelectorAll('input').find(c => selector !== 'input:checked' || c.checked);
  }
}
async function main() {
  const root = new Element('main'), error = new Element('p');
  let listener, copied, queued, fail = false;
  const parent = {postMessage(message) {
    if (!message.id) return;
    if (message.method === 'tools/call') queued = message.params.arguments;
    queueMicrotask(() => listener({source: parent, data: {jsonrpc: '2.0', id: message.id,
      result: message.method === 'tools/call' ? (fail ? {isError: true, content: [{text: 'uncertain'}], structuredContent: {retryable: false, copy_text: 'saved failure choice'}} : {structuredContent: {done: true, status: queued.action === 'confirm' ? 'confirmed' : 'submitted', copy_text: 'confirmed only'}}) : {}}}));
  }};
  const context = vm.createContext({parent, console, setTimeout, clearTimeout,
    navigator: {clipboard: {writeText: async text => { copied = text; }}},
    window: {addEventListener: (_, fn) => { listener = fn; }},
    ResizeObserver: class { observe() {} },
    document: {getElementById: id => id === 'items' ? root : error,
      createElement: tag => new Element(tag), createTextNode: text => text,
      documentElement: {style: {}}, body: new Element('body')}});
  const html = fs.readFileSync(require('node:path').join(__dirname, '../automation/monitor/widget.html'), 'utf8');
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1], context);
  const item = {id: 'id', summary: 'short', status: 'new',
    selection: '', copy_text: 'pending reference', decision: {question: 'fix one?', options: ['fix', 'later']},
    findings: [{title: 'a', reason: 'impact', evidence: []}, {title: 'b', reason: 'impact', evidence: []}]};
  context.data = {session_id: 'session', items: [item]};
  vm.runInContext('render(data)', context);
  const row = root.children[0], details = row.children.find(c => c.tag === 'details');
  assert.ok(details && !details.open);
  const choices = row.children.find(c => c.tag === 'fieldset');
  assert.equal(choices.querySelectorAll('input').length, 3); // Fixed Other.
  choices.querySelectorAll('input')[2].checked = true;
  choices.children.at(-1).value = 'my choice';
  const actions = row.children.find(c => c.className === 'actions');
  const buttons = actions.querySelectorAll('button');
  await buttons[1].events.click();
  assert.equal(queued.action, 'confirm');
  assert.ok(!choices.disabled && buttons.every(c => !c.disabled));
  await buttons[0].events.click();
  assert.equal(queued.selection, 'my choice');
  assert.equal(queued.other, true);
  assert.ok(choices.disabled && buttons.every(c => c.disabled));
  assert.equal(root.children[0], row);
  const copy = row.children.find(c => c.tag === 'button');
  assert.ok(!copy.disabled);
  await copy.events.click();
  assert.equal(copied, 'confirmed only');
  fail = true;
  vm.runInContext('render(data)', context);
  const failedRow = root.children[0];
  const failedChoices = failedRow.children.find(c => c.tag === 'fieldset');
  failedChoices.querySelectorAll('input')[0].checked = true;
  await failedRow.children.find(c => c.className === 'actions').querySelectorAll('button')[0].events.click();
  await failedRow.children.find(c => c.tag === 'button').events.click();
  assert.equal(copied, 'saved failure choice');
  assert.ok(failedChoices.disabled);
  context.data.items = [{...item, decision: null, status: 'acknowledged'}];
  vm.runInContext('render(data)', context);
  assert.ok(!root.children[0].children.some(c => c.tag === 'fieldset'));
  console.log('monitor widget fixture passed');
}
main().catch(e => { console.error(e); process.exitCode = 1; });
