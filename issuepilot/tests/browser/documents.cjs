/* Test parsed structure & content of every runtime section with the shipped Markdown library. */
const {test}=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), path=require('node:path'), vm=require('node:vm');
const app=path.resolve(__dirname,'../..'), pub=path.join(app,'src/issuepilot/static');
class Tree {
  constructor(tag,text=''){this.tag=tag;this.value=text;this.children=[];}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  set textContent(value){this.value=value;this.children=[];}
  get textContent(){return this.value+this.children.map(n=>n.textContent).join('');}
  find(tag){return [...(this.tag===tag?[this]:[]),...this.children.flatMap(n=>n.find(tag))];}
}
const document={createDocumentFragment:()=>new Tree('fragment'),createElement:t=>new Tree(t),createTextNode:s=>new Tree('text',s)};
const context=vm.createContext({document,URL});context.window=context;
for(const name of ['vendor/marked-17.0.5.js','document.js'])vm.runInContext(fs.readFileSync(path.join(pub,name),'utf8'),context);
const render=context.issueDocument.render;
test('all normative tables preserve every source row and last condition',()=>{
  const md=fs.readFileSync(path.join(app,'policies/runtime/issuepilot_policy.md'),'utf8');
  const sections=md.split(/^## (IP-[A-Z]+-\d{3}) — /gm);
  let tables=0;
  for(let i=1;i<sections.length;i+=2){
    const text=sections[i+1].trim(), doc=render(text,{category:'bug'});
    const sourceRows=text.split('\n').filter(l=>l.startsWith('|')&&!/^\|[\s:|-]+\|$/.test(l));const htmlRows=doc.find('tr');
    assert.equal(htmlRows.length,sourceRows.length,sections[i]);tables+=doc.find('table').length;
    for(let j=0;j<sourceRows.length;j++)assert.equal(htmlRows[j].children.length,sourceRows[j].trim().slice(1,-1).split('|').length);
    if(sections[i]==='IP-INFO-001'){assert.ok(text.length>1000);assert.ok(doc.textContent.includes(text.slice(-90)));assert.equal(doc.find('tr').filter(n=>n.className==='policy-current').length,1);}
  }
  assert.ok(tables>=3);
});
test('original ticket semantics: N/A, inline and fenced code, lists and guarded links',()=>{
  const d=render('N/A — browser.\n\n**Observed:** 20 → 35.\n\n1. Click `Save`.\n2. Open the [guide](../guide).\n\n```text\nx | y -- z\n```\n\n![screenshot](https://example.org/img) [archive](ftp://example.org/a)',{base:'https://github.com/example/test/issues/1'});
  assert.equal(d.find('ol').length,1);assert.equal(d.find('li').length,2);assert.equal(d.find('strong')[0].textContent,'Observed:');assert.equal(d.find('pre')[0].textContent,'x | y -- z');assert.ok(d.textContent.includes('N/A'));
  assert.equal(d.find('img').length,0);assert.ok(d.find('a').every(n=>n.href.startsWith('https://')));assert.ok(d.find('a').some(n=>n.href.endsWith('/guide')));
});
test('GitHub system diagnostics: disclosure around table, line break and linked attachment',()=>{
  const d=render('<details>\n<summary>System Info</summary>\n\n|Item|Value|\n|---|---|\n|GPU|first<br>second|\n\n</details>\n\n<img width="900" alt="Capture" src="https://example.org/capture" />');
  assert.equal(d.find('details').length,1);assert.equal(d.find('details')[0].find('table').length,1);assert.equal(d.find('summary')[0].textContent,'System Info');assert.equal(d.find('br').length,1);
  assert.equal(d.find('img').length,0);assert.equal(d.find('a')[0].textContent,'Attachment: Capture');assert.equal(d.find('a')[0].href,'https://example.org/capture');
});
if(process.env.ISSUEPILOT_ISSUE_DATA) test('render the pinned source corpus, with no parser fallback or automatic media',()=>{
  let count=0, fallback=0, disclosed=0,linkedAttachments=0;
  const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../../src/issuepilot/assets/issue-source.json'),'utf8'));
  const prior=Tree.prototype.replaceChildren;
  Tree.prototype.replaceChildren=function(...nodes){fallback++;prior.apply(this,nodes);};
  try {
    for(const part of Object.keys(manifest.splits)){
      const lines=fs.readFileSync(path.join(process.env.ISSUEPILOT_ISSUE_DATA,part+'.jsonl'),'utf8').trimEnd().split(/\r?\n/);
      for(const line of lines){
        const row=JSON.parse(line), body=row.input.slice(row.input.indexOf('\n\nDescription:')+'\n\nDescription:'.length);
        const doc=render(body,{base:'https://github.com/microsoft/vscode/issues/'+row.issue_number});count++;
        assert.equal(doc.find('img').length,0);if(doc.find('details').length)disclosed++;
        if(doc.find('a').some(n=>n.textContent.startsWith('Attachment:')))linkedAttachments++;
      }
    }
  } finally {Tree.prototype.replaceChildren=prior;}
  assert.equal(count,Object.values(manifest.splits).reduce((a,b)=>a+b,0));assert.equal(fallback,0);console.log({count,disclosed,linkedAttachments,fallback});
});
