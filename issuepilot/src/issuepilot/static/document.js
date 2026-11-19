/* Marked is used strictly as a lexer. Only these DOM elements may be constructed.
 * Source and policy text is never parsed as HTML or fetched as external media. */
"use strict";
window.issueDocument = (() => {
  const el = tag => document.createElement(tag);
  const txt = value => document.createTextNode(value || "");
  const entities = {amp:"&", lt:"<", gt:">", quot:'"', apos:"'", nbsp:" "};
  const decode = s => (s || "").replace(/&(amp|lt|gt|quot|apos|nbsp|#\d+|#x[\da-f]+);/gi, (all, k) => {
    if (k[0] !== "#") return entities[k.toLowerCase()] ?? all;
    const i = k[1].toLowerCase() === "x" ? parseInt(k.slice(2),16) : Number(k.slice(1));
    return i > 0 && i <= 0x10ffff && !(i >= 0xd800 && i <= 0xdfff) ? String.fromCodePoint(i) : all;
  });
  function link(href, base) {
    const input = decode(href).trim();
    try {
      if (!input || /[\u0000-\u001f\\]/.test(input)) return null;
      const u = new URL(input, base || undefined);
      return ["https:", "http:"].includes(u.protocol) && !u.username && !u.password ? u.href : null;
    } catch { return null; }
  }
  function render(source, options = {}) {
    const fragment = document.createDocumentFragment();
    function walk(tokens, parent, depth = 0) {
      // A GitHub diagnostics disclosure can enclose several separate Markdown blocks.
      // Parse only a small set of inert tags; never hand user HTML to the browser.
      const frames = [];
      const inlineTags = {b:"strong",strong:"strong",i:"em",em:"em",details:"details",summary:"summary",pre:"pre",code:"code",kbd:"kbd",p:"p",sup:"sup",sub:"sub"};
      const html = raw => {
        const pieces=raw.match(/<!--[\s\S]*?-->|<\/?[a-zA-Z][^<>"']*(?:(?:"[^"]*"|'[^']*')[^<>"']*)*>|[^<]+|</g) || [];
        for(const piece of pieces){
          if(piece.startsWith("<!--")) continue;
          const tag=piece.match(/^<(\/?)([a-z][a-z0-9-]*)\b[\s\S]*>$/i);
          if(!tag){parent.append(txt(decode(piece)));continue;}
          const name=tag[2].toLowerCase();
          if(name==="br"){if(!tag[1])parent.append(el("br"));continue;}
          if(name==="img" && !tag[1]){
            const attrs={};
            for(const m of piece.matchAll(/\s+(alt|src)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/gi))attrs[m[1].toLowerCase()]=m[2]??m[3]??m[4];
            const href=link(attrs.src || "",options.base),a=el(href?"a":"span");
            if(href){a.href=href;a.target="_blank";a.rel="noopener noreferrer";}
            a.textContent="Attachment: "+(decode(attrs.alt) || "view source");parent.append(a);continue;
          }
          if(!Object.hasOwn(inlineTags,name)){parent.append(txt(piece));continue;}
          if(tag[1]) {
            if(frames.length && frames[frames.length-1].name===name)parent=frames.pop().previous;
            continue;
          }
          if(frames.length>=50){parent.append(txt(piece));continue;}
          const next=el(inlineTags[name]);parent.append(next);
          frames.push({name,previous:parent});parent=next;
        }
      };
      for (const t of tokens || []) {
        if (depth > 60) { parent.append(txt(t.raw)); continue; }
        let n;
        const child = (target, list=t.tokens) => list ? walk(list,target,depth+1) : target.append(txt(decode(t.text)));
        switch (t.type) {
          case "space": continue;
          case "heading": n=el("h"+Math.min(6, Math.max(3, t.depth+2))); child(n); break;
          case "strong": case "em": case "del": case "blockquote": n=el(t.type); child(n); break;
          case "paragraph": n=el("p"); child(n); break;
          case "text": if (t.tokens) { walk(t.tokens,parent,depth+1); continue; } n=txt(decode(t.text)); break;
          case "escape": n=txt(t.text); break;
          case "codespan": n=el("code"); n.textContent=t.text; break;
          case "code": { n=el("pre"); const code=el("code"); code.textContent=t.text; n.append(code); break; }
          case "br": case "hr": n=el(t.type); break;
          case "link": case "image": {
            const href=link(t.href,options.base); n=el(href?"a":"span");
            if(href){ n.href=href; n.target="_blank"; n.rel="noopener noreferrer"; }
            if(t.type==="image") n.textContent="Attachment: "+(t.text || "open source"); else child(n);
            break;
          }
          case "list": {
            n=el(t.ordered?"ol":"ul");
            if(t.ordered && Number.isSafeInteger(t.start)) n.start=t.start;
            for(const item of t.items){const li=el("li"); child(li,item.tokens); n.append(li);} break;
          }
          case "checkbox": n=el("input");n.type="checkbox";n.disabled=true;n.checked=Boolean(t.checked);break;
          case "table": {
            n=el("div");n.className="md-table";const table=el("table");
            const line=(row,head,target)=>{const tr=el("tr");for(const cell of row){const td=el(head?"th":"td");if(head)td.scope="col";child(td,cell.tokens);tr.append(td);}
              if(!head && options.category && row[0].text===options.category)tr.className="policy-current";target.append(tr);};
            const thead=el("thead"),tbody=el("tbody");line(t.header,true,thead);
            for(const row of t.rows)line(row,false,tbody);table.append(thead,tbody);n.append(table);break;
          }
          case "html": html(t.raw); continue;
          default: n=txt(t.raw || t.text);
        }
        parent.append(n);
      }
    }
    try { walk(window.marked.lexer(source || "",{gfm:true,breaks:true}),fragment); }
    catch {fragment.replaceChildren(txt(source));}
    return fragment;
  }
  return {render};
})();
