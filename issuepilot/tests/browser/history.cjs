const {test}=require('node:test'),assert=require('node:assert/strict');
const {createCaseReader}=require('../../src/issuepilot/static/history.js');
test('Inbox A then B: late A response can never replace B; refresh stays scoped to B',async()=>{
  const pending=[],rendered=[],errors=[];
  const reader=createCaseReader({read:id=>new Promise(resolve=>pending.push([id,resolve])),loading:()=>{},render:c=>rendered.push(c.id),failure:e=>errors.push(e.message)});
  const a=reader.open('a'),b=reader.open('b');
  pending[1][1]({id:'b'});await b;pending[0][1]({id:'a'});await a;
  assert.deepEqual(rendered,['b']);const q=reader.refresh();assert.equal(pending[2][0],'b');pending[2][1]({id:'other'});await q;
  assert.equal(errors.length,1);assert.deepEqual(rendered,['b']);
});
test('closing discards in-flight results; reopening from Execution trace reads its own case',async()=>{
  const pending=[],rendered=[];
  const r=createCaseReader({read:id=>new Promise(resolve=>pending.push([id,resolve])),loading:()=>{},render:c=>rendered.push(c.id),failure:e=>assert.fail(e.message)});
  const abandoned=r.open('completed');r.close();assert.equal(r.id(),null);
  pending[0][1]({id:'completed'});await abandoned;
  const q=r.open('review-case');pending[1][1]({id:'review-case'});await q;
  assert.deepEqual(rendered,['review-case']);assert.equal(r.id(),'review-case');
});
