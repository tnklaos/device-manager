const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../electron/renderer/app.js'), 'utf8');
const modal = source.split('// ---------- credentials modal ----------')[1]
  .split('// ---------- activity log dock ----------')[0];

function fixture(device) {
  const elements = {};
  const posts = [];
  const context = vm.createContext({
    devices: [{ serial: 'test', ...device }],
    $: (selector) => elements[selector] ||= { value: '', classList: { add() {}, remove() {} } },
    post: async (url, body) => posts.push({ url, body }),
    loadDevices() {},
  });
  vm.runInContext(modal, context);
  context.openModal('test');
  return { elements, posts };
}

test('modal displays the scan stop, not the older delivery watermark', async () => {
  const { elements, posts } = fixture({ scan_ref: '130', last_ref: '127' });
  assert.equal(elements['#m-ref'].value, '130');
  await elements['#m-save'].onclick();
  assert.equal(Object.hasOwn(posts[0].body, 'last_ref'), false);
  assert.equal(Object.hasOwn(posts[0].body, 'password'), false);
});

test('only an explicit reference edit resets the boundary', async () => {
  const { elements, posts } = fixture({ scan_ref: '130', last_ref: '127' });
  elements['#m-ref'].value = '';
  await elements['#m-save'].onclick();
  assert.equal(posts[0].body.last_ref, '');
});

test('older backends still populate the reference field', () => {
  const { elements } = fixture({ last_ref: '127' });
  assert.equal(elements['#m-ref'].value, '127');
});
