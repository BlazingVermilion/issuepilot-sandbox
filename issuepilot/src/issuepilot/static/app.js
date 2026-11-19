/* UI state is a view of backend records. Never infer execution from an optimistic click. */
"use strict";
const $ = (id) => document.getElementById(id);
let workspace = null,
  cases = [],
  active = null,
  currentCatalog = null,
  token = "",
  session = 0,
  busy = false,
  dirty = false,
  pendingApproval = null;
const tabs = ["overview", "inbox", "review", "repository"];
const attention = ["STALE", "FAILED", "REPLY_FAILED", "UNCERTAIN", "BLOCKED"];
const processing = [
  "QUEUED",
  "RESUME_QUEUED",
  "REPLY_QUEUED",
  "PROCESSING",
  "APPROVED",
  "EXECUTING",
  "REJECTED",
];
const names = {
  REPLY_FAILED: "Reporter processing failed",
  WAITING_REPORTER: "Awaiting reporter",
  REPLY_QUEUED: "Reporter update queued",
  QUEUED: "Queued",
  BLOCKED: "Write rejected",
  RESUME_QUEUED: "Recovery queued",
  PROCESSING: "Investigating",
  REVIEW: "Awaiting review",
  APPROVED: "Approved",
  EXECUTING: "Executing",
  COMPLETED: "Completed",
  STALE: "Review expired",
  FAILED: "Failed",
  UNCERTAIN: "Needs reconciliation",
  REJECTED: "Rejection pending",
  DECLINED: "Declined",
  CLOSED: "Issue closed",
};
function node(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  return n;
}
function tell(message, error = false) {
  $("notice").hidden = false;
  $("notice").textContent = message;
  $("notice").className = "notice " + (error ? "error" : "info");
}
function error(e) {
  if (e.name !== "AbortError") tell(e.message, true);
}
function statusBadge(status) {
  return node(
    "span",
    names[status] || status,
    "badge " +
      (status === "FAILED"
        ? "error"
        : attention.includes(status)
          ? "warn"
          : ["QUEUED", "PROCESSING", "DECLINED", "CLOSED"].includes(status)
            ? "neutral"
            : ""),
  );
}
function date(value) {
  return value ? new Date(value).toLocaleString() : "";
}
function chip(text) {
  return node("span", text, "chip");
}
function tab(name, focus = false) {
  if (name === "activity") name = "inbox";
  if (!tabs.includes(name)) name = "overview";
  for (const t of tabs) {
    const on = t === name;
    $("panel-" + t).hidden = !on;
    $("tab-" + t).setAttribute("aria-selected", String(on));
    $("tab-" + t).tabIndex = on ? 0 : -1;
  }
  $("notice").hidden = true;
  history.replaceState(null, "", "#" + name);
  if (focus) $("tab-" + name).focus();
  if (name === "repository" && workspace) loadLabels().catch(error);
}
for (const b of document.querySelectorAll("[data-tab]"))
  b.onclick = () => tab(b.dataset.tab);
for (const b of document.querySelectorAll("[data-go]"))
  b.onclick = () => tab(b.dataset.go);
document.querySelector("nav").addEventListener("keydown", (e) => {
  if (
    ![
      "ArrowDown",
      "ArrowUp",
      "ArrowRight",
      "ArrowLeft",
      "Home",
      "End",
    ].includes(e.key)
  )
    return;
  e.preventDefault();
  let i = tabs.indexOf(document.activeElement.dataset.tab);
  if (e.key === "Home") i = 0;
  else if (e.key === "End") i = tabs.length - 1;
  else
    i =
      (i +
        (["ArrowDown", "ArrowRight"].includes(e.key) ? 1 : -1) +
        tabs.length) %
      tabs.length;
  tab(tabs[i], true);
});
function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme").querySelector("span").textContent =
    theme === "dark" ? "Light mode" : "Dark mode";
  $("theme").setAttribute(
    "aria-label",
    "Switch to " + (theme === "dark" ? "light" : "dark") + " mode",
  );
  try {
    localStorage.setItem("issuepilot.theme", theme);
  } catch {}
}
let initialTheme = "light";
try {
  initialTheme =
    localStorage.getItem("issuepilot.theme") ||
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
} catch {}
setTheme(initialTheme);
$("theme").onclick = () =>
  setTheme(
    document.documentElement.dataset.theme === "dark" ? "light" : "dark",
  );
async function api(path, method = "GET", body) {
  const epoch = session;
  const response = await fetch(path, {
    method,
    headers: {
      Authorization: "Bearer " + token,
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (epoch !== session)
    throw new DOMException("Session changed", "AbortError");
  if (response.status === 401) {
    clearSession();
    throw Error("Session credentials are no longer valid. Connect again.");
  }
  if (!response.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail || data),
    );
  return data;
}
function counts() {
  for (const [id, predicate] of [
    ["review", (c) => c.status === "REVIEW"],
    ["processing", (c) => processing.includes(c.status)],
    ["complete", (c) => c.status === "COMPLETED"],
    ["attention", (c) => attention.includes(c.status)],
  ])
    $("metric-" + id).textContent = cases.filter(predicate).length;
  $("nav-count").textContent = cases.filter(
    (c) => c.status === "REVIEW",
  ).length;
}
let inboxPage = 0;
const pageSize = 25;
function renderInbox() {
  const q = $("search").value.toLowerCase();
  const filter = $("filter").value;
  const filtered = cases.filter(
    (c) =>
      (c.title.toLowerCase().includes(q) || String(c.number).includes(q)) &&
      (filter === "all" ||
        (filter === "attention" && attention.includes(c.status)) ||
        c.status === filter),
  );
  $("case-rows").replaceChildren();
  $("inbox-empty").hidden = filtered.length > 0;
  const empty = $("inbox-empty");
  empty.querySelector("h3").textContent = workspace
    ? cases.length
      ? "No matching issues"
      : "Your inbox is empty"
    : "No cases loaded";
  empty.querySelector("p").textContent = workspace
    ? workspace.mode === "live"
      ? "Create an issue on GitHub after configuring its webhook. Then refresh here."
      : "Create a sample using the demo form below."
    : "Connect your reviewer account to begin.";
  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  inboxPage = Math.max(0, Math.min(inboxPage, pageCount - 1));
  const from = inboxPage * pageSize;
  $("inbox-pages").hidden = !filtered.length;
  $("page-range").textContent = filtered.length ? `${from + 1}–${Math.min(from + pageSize, filtered.length)} of ${filtered.length} matching cases` : "";
  $("page-prev").disabled = inboxPage === 0;
  $("page-next").disabled = inboxPage + 1 >= pageCount;
  if (workspace?.dataset) empty.querySelector("p").textContent = "Replay the pinned VS Code dataset or change the filters.";
  for (const c of filtered.slice(from, from + pageSize)) {
    const tr = node("tr");
    const title = node("td");
    title.append(
      node("span", "#" + c.number + " · version " + c.version, "issue-num"),
      node("span", c.title, "issue-name"),
    );
    const state = node("td");
    state.append(statusBadge(c.status));
    const button = node(
      "button",
      c.status === "REVIEW" ? "Review →" : "Inspect →",
      "button quiet",
    );
    button.onclick = () => selectCase(c.id).catch(error);
    const action = node("td");
    const history = node("button", "Open audit history", "button quiet");
    history.setAttribute("aria-label", "Open audit history for issue #" + c.number);
    history.setAttribute("aria-haspopup", "dialog");
    history.onclick = () => openAudit(c.id, history);
    const controls = node("div", undefined, "row-actions");
    if (!["COMPLETED", "CLOSED", "DECLINED"].includes(c.status)) controls.append(button);
    controls.append(history); action.append(controls);
    tr.append(title, state, node("td", date(c.updated_at), "subtle"), action);
    $("case-rows").append(tr);
  }
}
function overview() {
  counts();
  $("next-action").disabled = !workspace;
  if (!workspace) return;
  const waiting = cases.find((c) => c.status === "REVIEW");
  $("next-title").textContent = waiting
    ? "A proposal is ready for your review"
    : cases.length
      ? "Follow your case through the workflow"
      : "Receive your first issue";
  $("next-copy").textContent = waiting
    ? "Read the source and policy, adjust the proposal if needed, then approve or reject."
    : workspace.mode === "live"
      ? "Create an issue on the configured GitHub repository. Its signed webhook must reach n8n and the backend."
      : "Use the local sample form in Inbox. Demo actions stay in the database.";
  if (workspace.dataset && !waiting) $("next-copy").textContent = "Open Inbox to inspect historical VS Code reports received in this sandbox.";
  $("next-action").textContent = waiting
    ? "Review issue #" + waiting.number + " →"
    : "Open inbox →";
  $("next-action").onclick = () =>
    waiting ? selectCase(waiting.id).catch(error) : tab("inbox");
  $("mode-note").textContent =
    workspace.mode === "demo"
      ? "DEMO · Actions are simulated in the database. " +
        (workspace.engine === "rules"
          ? "The rules engine does not call an LLM."
          : "Model: " + workspace.model + ". GitHub writes remain simulated.")
      : "LIVE · " +
        (workspace.engine === "rules"
          ? "Rules baseline; no LLM calls. "
          : "Model: " + workspace.model + ". ") +
        (workspace.writes_enabled
          ? "Approved actions can write to GitHub."
          : "GitHub writes are disabled.");
  if (workspace.dataset) $("mode-note").textContent = `VS Code dataset replay · ${cases.length} / ${workspace.dataset.total} issues received · ${workspace.model} · actions stay in this sandbox.`;
}
function renderSettings() {
  if (!workspace) return;
  $("repo-name").textContent = workspace.repo;
  $("runtime").textContent = workspace.mode + " / " + (workspace.model || "Rules demo");
  $("identity").textContent = workspace.reviewer;
  $("disconnect").hidden = false;
  $("login-panel").hidden = true;
  $("session-heading").hidden = false;
  $("token").value = "";
  $("connection-summary").hidden = false;
  $("connection-summary").textContent =
    "Connected as " + workspace.reviewer + " · " + workspace.repo;
  $("demo-box").hidden = workspace.mode !== "demo" || Boolean(workspace.dataset);
  $("new-github").hidden = workspace.mode !== "live";
  if (workspace.repo_url)
    $("new-github").href = workspace.repo_url + "/issues/new";
  $("intake-note").textContent =
    workspace.mode === "live"
      ? "Live intake: create an issue on GitHub → n8n receives its webhook → the worker drafts a proposal. This page does not create GitHub issues."
      : "Demo intake: use the sample form below. This path bypasses GitHub webhooks and n8n.";
  if (workspace.dataset) $("intake-note").textContent = "Historical VS Code reports · signed dataset replay · original issue numbers · no writes to GitHub.";
  const grid = node("div", undefined, "settings-grid");
  for (const [label, value] of [
    ["Repository", workspace.repo],
    ["Mode", workspace.mode],
    ["Engine", workspace.engine],
    ["AI model", workspace.model || "None — rules baseline"],
    ["Inference profile", workspace.ai_profile || "Rules baseline"],
    ["Database", workspace.database],
    [
      "GitHub writes",
      workspace.mode === "demo"
        ? "Simulated"
        : workspace.writes_enabled
          ? "Enabled after approval"
          : "Disabled",
    ],
  ]) {
    const div = node("div");
    div.append(node("span", label), node("strong", value));
    grid.append(div);
  }
  $("repo-settings").replaceChildren(grid);
  overview();
}
async function loadLabels() {
  if (!workspace) throw Error("Connect as a reviewer first.");
  currentCatalog = await api("/repository/labels");
  $("catalog-source").textContent =
    currentCatalog.availability_source === "github"
      ? "IssuePilot policy · GitHub availability"
      : "IssuePilot policy · sandbox";
  $("catalog-rows").replaceChildren();
  for (const label of currentCatalog.labels) {
    const tr = node("tr"),
      name = node("td"),
      badge = chip(label.name),
      swatch = node("span", undefined, "label-swatch");
    if (/^[a-fA-F0-9]{6}$/.test(label.color))
      swatch.style.backgroundColor = "#" + label.color;
    badge.prepend(swatch);
    name.append(badge, node("small", label.group, "label-group"));
    const permission = node("td");
    permission.append(
      node(
        "span",
        label.agent_allowed
          ? "May propose · approval required"
          : !label.available ? "Not provisioned on GitHub" : "Policy only · not an intake suggestion",
        "badge " + (label.agent_allowed ? "" : "neutral"),
      ),
    );
    tr.append(name, node("td", label.description), permission);
    $("catalog-rows").append(tr);
  }
  $("catalog-note").textContent =
    currentCatalog.labels.length +
    " labels · " +
    currentCatalog.allowed.length +
    " permitted for proposals · fetched " +
    new Date().toLocaleTimeString();
  $("label-warning").hidden = !currentCatalog.legacy_filter_active;
  $("label-warning").textContent =
    "IP_ALLOWED_LABELS is applying an additional legacy filter. Remove that variable from .env and recreate api/worker to use the policy permissions and available labels.";
}
async function loadCases() {
  cases = await api("/cases");
  renderInbox();
  overview();
  $("last-refresh").textContent = "Updated " + new Date().toLocaleTimeString();
}
let reviewRequest = 0;
async function selectCase(id) {
  if (
    dirty &&
    !window.confirm(
      "Discard your unsaved proposal edits and open another case?",
    )
  )
    return;
  const seq = ++reviewRequest;
  const result = await api("/cases/" + encodeURIComponent(id));
  if (seq !== reviewRequest) return;
  active = result;
  dirty = false;
  renderCase();
  tab("review");
}
function renderCase() {
  if (!active) return;
  const c = active,
    p =
      c.status === "REVIEW"
        ? c.proposal.value
        : c.decision.proposal || c.proposal.value;
  $("review-empty").hidden = true;
  $("review-content").hidden = false;
  $("case-status").replaceWith(
    Object.assign(statusBadge(c.status), { id: "case-status" }),
  );
  $("case-meta").textContent = (workspace?.dataset ? "VS Code snapshot · " : "") + "#" + c.number + " · version " + c.version;
  $("issue-link").textContent = workspace?.dataset ? "Original VS Code issue ↗" : "Open on GitHub ↗";
  $("issue-title").textContent = c.source.title || "Waiting for source read";
  const src = c.source || {};
  const hasBody = Object.hasOwn(src,"body");
  const raw = src.body ?? "";
  $("source-repo").textContent = "Source issue · " + (workspace.dataset ? "microsoft / vscode" : workspace.repo);
  $("source-number").textContent = "#" + c.number;
  $("source-state").textContent = src.state === "closed" ? "Closed" : src.state === "open" ? "Open" : "State not recorded";
  $("source-state").className = "badge ticket-state " + (src.state || "unknown");
  $("source-byline").textContent = [src.author ? "@" + src.author : "", src.created_at ? date(src.created_at) : ""].filter(Boolean).join(" · ");
  $("issue-body").replaceChildren(raw ? issueDocument.render(raw,{base:c.issue_url}) : node("p",hasBody ? "No description provided." : "No source snapshot yet.","hint"));
  $("raw-body").textContent = raw;
  $("raw-body").parentElement.hidden = !raw;
  $("source-labels").replaceChildren(...(c.source.labels || []).map(chip));
  $("issue-link").hidden = !c.issue_url;
  if (c.issue_url) $("issue-link").href = c.issue_url;
  const proposed = [...c.events]
    .reverse()
    .find((e) => e.kind === "PROPOSED" && e.data.version === c.version);
  $("engine-proof").textContent = proposed
    ? proposed.data.engine === "rules"
      ? "Rules baseline · no LLM call"
      : "Ollama · " + proposed.data.model
    : "No proposal for this version";
  const guidance = {
    REPLY_FAILED: "Repair the read/model error, then Check reporter replies. The saved clarification and completed writes are retained.",
    WAITING_REPORTER: workspace.dataset ? "Clarification recorded in the sandbox. The dataset has no subsequent reporter reply." : "Clarification sent. Await the issue author’s reply; new evidence requires a fresh maintainer approval.",
    REPLY_QUEUED: "Reading the reporter’s latest evidence and resuming the saved graph.",
    REVIEW: p?.abstain
      ? "The engine abstained. No executable actions are proposed. Reject and investigate manually."
      : "Inspect the source and cited policy. You may edit the comment and permitted labels before approval.",
    BLOCKED:
      "GitHub rejected a write. Fix permissions or configuration, then recover remaining work.",
    RESUME_QUEUED:
      "Recovery is queued. Completed actions will not be sent again.",
    QUEUED: "Queued for the worker. This page refreshes while processing.",
    PROCESSING: "The worker is investigating. No action has been approved.",
    APPROVED:
      "Approval recorded. Waiting for the worker to recheck freshness and execute.",
    EXECUTING: "The backend is executing approved actions. Do not resend them.",
    COMPLETED:
      workspace.mode === "demo"
        ? "Completed in the local simulation. No comment was sent to GitHub."
        : "Execution completed. Open the GitHub issue to verify the recorded actions.",
    STALE:
      "The source, policy or label catalog changed. Generate a fresh proposal and review it again.",
    FAILED:
      "Processing failed. Fix the cause, then use recovery if an action ledger exists; otherwise generate a fresh proposal.",
    UNCERTAIN:
      "An external write has an uncertain result. Check existing actions; reconciliation never blindly retries a write.",
    DECLINED:
      "Proposal rejected. Generate another only if you want to reconsider.",
    REJECTED: "Rejection recorded. The worker will finish the paused workflow.",
    CLOSED: "The source issue is closed. No action is proposed.",
  };
  $("case-guidance").textContent =
    (guidance[c.status] || c.status) + (c.error ? " " + c.error : "");
  $("proposal-category").textContent = p?.category || "Pending";
  $("proposal-summary").textContent =
    p?.summary || "A proposal will appear after investigation.";
  $("proposal-routing").textContent = `Area: ${p?.feature_area || "unknown"} · Next: ${p?.next_step || "pending"} · Clarification rounds: ${c.proposal.rounds || 0}`;
  const replies = c.source.reporter_replies || [];
  $("reporter-evidence").replaceChildren(...(replies.length ? replies.map(r => {
    const item=node("article"); item.append(issueDocument.render(r.body,{base:c.issue_url})); return item;
  }) : [node("p","No reporter replies in this snapshot.","hint")]));
  $("proposal-reason").textContent = p?.reason || "Not available yet.";
  $("missing").replaceChildren(
    ...(p?.missing_fields?.length
      ? p.missing_fields.map(chip)
      : [node("span", p ? "None identified" : "Not assessed", "subtle")]),
  );
  $("comment").value = p?.comment || "";
  $("comment").disabled = c.status !== "REVIEW" || busy;
  $("assignees").replaceChildren(
    ...(p?.assignees?.length
      ? p.assignees.map(chip)
      : [node("span", "No assignment proposed", "subtle")]),
  );
  $("label-options").replaceChildren();
  const captured = c.proposal.label_catalog;
  const legacyCatalog = Boolean(captured && captured.source !== "issuepilot_policy");
  const allowed = legacyCatalog ? [] : captured?.allowed || [];
  for (const label of [...new Set([...allowed, ...(p?.labels || [])])]) {
    const wrapper = node("label", undefined, "chip");
    const check = node("input");
    check.type = "checkbox";
    check.value = label;
    check.checked = (p?.labels || []).includes(label);
    check.disabled = c.status !== "REVIEW" || !allowed.includes(label) || label !== p?.category || busy;
    check.onchange = () => {
      dirty = true;
    };
    wrapper.append(
      check,
      node("span", label + (allowed.includes(label) ? "" : " · unavailable")),
    );
    if(c.status !== "REVIEW" && !(p?.labels || []).includes(label)) continue;
    $("label-options").append(wrapper);
  }
  if (!allowed.length)
    $("label-options").append(
      node("span", "No permitted labels in this proposal snapshot.", "subtle"),
    );
  $("approve").disabled =
    busy ||
    c.status !== "REVIEW" ||
    !p ||
    p.abstain ||
    (!captured || legacyCatalog) ||
    (workspace.mode === "live" && !workspace.writes_enabled);
  $("reject").disabled = busy || c.status !== "REVIEW";
  $("approval-note").textContent =
    (!captured || legacyCatalog) && p && c.status === "REVIEW"
      ? "This proposal predates the current label policy. Generate a fresh proposal before approval."
      : workspace.mode === "live" && !workspace.writes_enabled
        ? "Live writes are disabled in server configuration."
        : workspace.mode === "demo"
          ? "Approval executes simulated actions only."
          : "Approval applies to this exact proposal version and may publish to GitHub.";
  $("evidence-count").textContent = "(" + (p?.citations?.length || 0) + ")";
  $("open-evidence").onclick = () => openEvidence(c,p);
  $("tool-trace").replaceChildren();
  for (const t of c.proposal.trace || []) {
    const item = node("div", undefined, "tool-item");
    item.append(
      node("strong", t.tool),
      node(
        "span",
        t.mode === "fixed_workflow"
          ? "Fixed workflow step — not LLM-selected"
          : t.tool === "classify_issue" ? t.category
          : t.tool === "search_policy" && t.context_ids ? t.decision + " · " + (t.context_ids.join(", ") || "No supporting sections")
          : t.tool === "label_permission" ? t.label + " · " + t.source
          : JSON.stringify(t.args || {}),
      ),
    );
    $("tool-trace").append(item);
  }
  if (!c.proposal.trace?.length)
    $("tool-trace").append(node("p", "No tool calls recorded.", "hint"));
  $("technical").textContent = JSON.stringify(
    {
      original_proposal: c.proposal,
      approved_decision: c.decision,
      policy_hash: c.policy_hash,
    },
    null,
    2,
  );
  const hasLedger = c.actions.some((a) => a.version === c.version);
  $("regenerate").hidden =
    hasLedger || !["STALE", "FAILED", "DECLINED", "REVIEW"].includes(c.status);
  $("recover").hidden =
    !hasLedger ||
    !["STALE", "FAILED", "BLOCKED", "UNCERTAIN"].includes(c.status);
  $("recover").disabled = busy;
  $("reconcile").hidden = !["UNCERTAIN", "BLOCKED"].includes(c.status);
  $("refresh-replies").hidden = Boolean(workspace.dataset) || !["WAITING_REPORTER", "REPLY_FAILED"].includes(c.status);
  $("demo-reply").hidden = c.status !== "WAITING_REPORTER" || workspace.mode !== "demo" || Boolean(workspace.dataset);
}
const dialogs = new Map();

function showReader(id, invoker) {
  const d=$(id);
  if (!d.open) { dialogs.set(id,invoker); d.showModal(); }
}
for(const id of ["audit-dialog","evidence-dialog"]) {
  const d=$(id);
  d.querySelector("[data-close]").onclick=()=>d.close();
  d.addEventListener("close",()=>{
    if(d.open) return;
    if(id==="audit-dialog"){auditReader.close();$("activity-content").hidden=true;}
    const button=dialogs.get(id); dialogs.delete(id);
    if(button?.isConnected && button.getClientRects().length) button.focus({preventScroll:true});
  });
}
function openEvidence(c,p) {
  const container=$("evidence");container.replaceChildren();
  $("evidence-case").textContent="#" + c.number+" · version "+c.version+" · Evidence saved with this proposal";
  for(const citation of p?.citations || []) {
    const d=(c.proposal.documents || []).find(x=>x.id===citation.id);
    const matched=Boolean(d?.text && citation.quote && d.text.includes(citation.quote));
    const item=node("article",undefined,"evidence-item");
    item.append(node("h3",citation.id + (d?.file ? " · "+d.file : "")));
    const body=node("div",undefined,"md-body");
    body.append(issueDocument.render(matched ? d.text : citation.quote,{category:p.category}));
    item.append(node("p",matched ? "Full section from the saved source." : "Saved excerpt only; a matching full section is unavailable.","hint"),body);
    if(matched && citation.quote!==d.text){
      const original=node("details");original.append(node("summary","Exact excerpt recorded with the proposal"),node("pre",citation.quote));item.append(original);
    }
    if(d?.sha256) {
      const details=node("details");details.append(node("summary","Source fingerprint"),node("code",d.sha256));item.append(details);
    }
    container.append(item);
  }
  if(!container.children.length) container.append(node("p","No policy citations in this proposal.","hint"));
  showReader("evidence-dialog",$("open-evidence")); $("evidence-dialog").scrollTop=0;
}
const auditReader = createCaseReader({
  read: id=>api("/cases/"+encodeURIComponent(id)),
  loading: (_id,invoker,refresh)=>{
    $("activity-content").hidden=true; $("audit-source").disabled=true;
    $("audit-message").textContent="Loading this issue’s history…";
    showReader("audit-dialog",invoker); if(!refresh) $("audit-dialog").scrollTop=0;
  },
  render: (c,refresh)=>{
    if(!$("audit-dialog").open) return;
    renderActivity(c);$("activity-content").hidden=false;
    if(!refresh) $("timeline-scroll").scrollTop=0;
    $("audit-source").disabled=false;$("audit-message").textContent="";
  },
  failure: e=>{$("audit-message").textContent="Could not load this history: "+e.message;}
});
const openAudit = (id,invoker)=>auditReader.open(id,invoker);
$("audit-refresh").onclick=()=>auditReader.refresh();
$("audit-source").onclick=()=>{
  const id=auditReader.id();
  if(id) {
    $("audit-dialog").close();
    if(id===active?.id) tab("review"); else selectCase(id).catch(error);
  }
};
$("trace-audit").onclick=()=>{if(active) openAudit(active.id,$("trace-audit"));};
function renderActivity(c) {
  $("activity-title").textContent =
    "#" + c.number + " · " + (c.source.title || "Pending issue");
  $("decision-summary").textContent = c.decision.reviewer
    ? `${c.decision.approved ? "Approved" : "Rejected"} by ${c.decision.reviewer} · version ${c.decision.version}`
    : "No human decision recorded.";
  $("approved-summary").replaceChildren();
  if (c.decision.proposal) {
    const p = c.decision.proposal;
    $("approved-summary").append(
      node("strong", c.decision.approved ? "Approved payload" : "Reviewed payload"),
      node("p", "Labels: " + (p.labels.join(", ") || "none")),
      node("p", p.comment || "No comment approved."),
    );
    const details = node("details");
    details.append(
      node("summary", "Compare original model proposal"),
      node("pre", JSON.stringify(c.proposal.value, null, 2)),
    );
    $("approved-summary").append(details);
  }
  $("action-results").replaceChildren();
  for (const a of c.actions) {
    const card = node("div", undefined, "action-result"),
      head = node("header");
    head.append(
      node("strong", a.kind + " · v" + a.version),
      node("span", a.status, "badge " + (a.status === "DONE" ? "" : "warn")),
    );
    const body=node(a.kind === "comment" ? "div" : "pre",undefined,"md-body");
    if(a.kind === "comment") body.append(issueDocument.render(a.payload.body,{base:c.issue_url}));
    else body.textContent=JSON.stringify(a.payload,null,2);
    card.append(head,body);
    $("action-results").append(card);
  }
  if (!c.actions.length)
    $("action-results").append(
      node("p", "No external actions recorded.", "hint"),
    );
  const scrollTop = $("timeline-scroll").scrollTop;
  $("timeline").replaceChildren();
  for (const e of [...c.events].reverse()) {
    const li = node("li");
    li.append(
      node("strong", e.kind.toLowerCase().replaceAll("_", " ")),
      node("time", date(e.at)),
    );
    const copy = {
      PROPOSED: `${e.data.engine === "rules" ? "Rules baseline" : e.data.model || "LLM"} · ${e.data.latency_ms || 0} ms · v${e.data.version}`,
      APPROVAL: `${e.data.reviewer} approved ${e.data.edited ? "an edited" : "the original"} proposal.`,
      REJECTION: `${e.data.reviewer} rejected the proposal.`,
      COMPLETED: "Recorded actions finished.",
      STALE: "The approval context changed. A new review is required.",
      RECEIVED: e.data.source_kind === "dataset_replay" ? "A signed VS Code snapshot replay was accepted. External writes are simulated." : "A signed GitHub delivery was accepted.",
      DEMO_EDIT: "A local demo issue was created or edited.",
    };
    if (copy[e.kind]) li.append(node("p", copy[e.kind]));
    const details = node("details");
    details.append(
      node("summary", "Event details"),
      node("pre", JSON.stringify(e.data, null, 2)),
    );
    li.append(details);
    $("timeline").append(li);
  }
  $("timeline-scroll").scrollTop = scrollTop;
}
$("comment").oninput = () => {
  dirty = true;
};
$("search").oninput = $("filter").onchange = () => { inboxPage = 0; renderInbox(); };
$("page-prev").onclick = () => { inboxPage--; renderInbox(); };
$("page-next").onclick = () => { inboxPage++; renderInbox(); };
// sessionStorage survives a refresh but is scoped to this browser tab.
// Never store the reviewer token in localStorage or include it in URLs.
const sessionKey = "issuepilot.reviewer-session";
function clearSession() {
  inboxPage = 0;
  recoveryTarget = null;
  session++;
  token = "";
  workspace = null;
  active = null;
  cases = [];
  currentCatalog = null;
  dirty = false;
  pendingApproval = null;
  try {
    sessionStorage.removeItem(sessionKey);
  } catch {}
  $("token").value = "";
  $("login-panel").hidden = false;
  $("session-heading").hidden = true;
  $("connection-summary").hidden = true;
  $("disconnect").hidden = true;
  $("identity").textContent = "Not connected";
  $("repo-name").textContent = "Connect your workspace";
  $("review-content").hidden = true;
  $("activity-content").hidden = true;
  $("review-empty").hidden = false;
  ++reviewRequest; auditReader.close();
  for(const id of ["audit-dialog","evidence-dialog"]) if($(id).open) $(id).close();
  for(const id of ["evidence","timeline","action-results","approved-summary","activity-title","issue-body","technical","raw-body"]) $(id).replaceChildren();
  $("demo-box").hidden = true;
  $("new-github").hidden = true;
  $("repo-settings").replaceChildren(
    node("p", "Connect to inspect configuration."),
  );
  $("catalog-rows").replaceChildren();
  $("confirm").close();
  $("recovery-dialog").close();
  renderInbox();
  overview();
  tab("overview");
}
async function connect(value, restoring = false) {
  session++;
  token = value;
  workspace = await api("/workspace");
  try {
    sessionStorage.setItem(sessionKey, token);
  } catch {
    tell("Connected for this page only: tab storage is unavailable.");
  }
  renderSettings();
  await loadCases();
  if (location.hash === "#repository") await loadLabels();
  if (!restoring)
    tell("Connected. Your session survives refreshes in this tab.");
}
$("connect-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    await connect($("token").value.trim());
  } catch (e) {
    clearSession();
    error(e);
  }
};
$("disconnect").onclick = () => {
  clearSession();
  tell("Disconnected. Enter your reviewer token to start a new session.");
};
$("refresh").onclick = async () => {
  try {
    if (!token) throw Error("Connect as a reviewer first.");
    workspace = await api("/workspace");
    renderSettings();
    await loadCases();
    if (active && !dirty) {
      active = await api("/cases/" + active.id);
      renderCase();
    } else if (dirty)
      tell("Inbox refreshed. Your unsaved edits were preserved.");
  } catch (e) {
    error(e);
  }
};
$("reload-labels").onclick = () => loadLabels().catch(error);
$("demo-form").onsubmit = async (e) => {
  e.preventDefault();
  if (busy) return;
  busy = true;
  try {
    const result = await api("/demo/issues", "POST", {
      number: Number($("demo-number").value),
      title: $("demo-title").value,
      body: $("demo-body").value,
    });
    await loadCases();
    await selectCase(result.case_id);
  } catch (e) {
    error(e);
  } finally {
    busy = false;
    if (active) renderCase();
  }
};
$("approve").onclick = () => {
  if (!active || busy) return;
  const p = structuredClone(active.proposal.value);
  p.comment = $("comment").value;
  p.labels = [...$("label-options").querySelectorAll("input:checked")].map(
    (x) => x.value,
  );
  pendingApproval = { id: active.id, version: active.version, proposal: p };
  $("confirm-title").textContent =
    workspace.mode === "demo"
      ? "Approve simulated actions?"
      : "Approve GitHub actions?";
  $("confirm-target").textContent =
    workspace.repo +
    " · issue #" +
    active.number +
    " · version " +
    active.version;
  $("confirm-labels").replaceChildren(...p.labels.map(chip));
  $("confirm-comment").textContent =
    p.comment || "No comment will be published.";
  $("confirm").returnValue = "";
  $("confirm").showModal();
};
$("confirm").addEventListener("close", async () => {
  if ($("confirm").returnValue !== "approve" || !pendingApproval) return;
  const saved = pendingApproval;
  pendingApproval = null;
  busy = true;
  try {
    const result = await api("/cases/" + saved.id + "/decision", "POST", {
      version: saved.version,
      approved: true,
      edited: saved.proposal,
    });
    dirty = false;
    active = await api("/cases/" + saved.id);
    renderCase();
    await loadCases();
    tell(
      result.state === "STALE"
        ? "The proposal is stale. Generate a fresh proposal before approving."
        : "Approval recorded. Open this issue’s audit history to see execution.",
    );
  } catch (e) {
    error(e);
  } finally {
    busy = false;
    if (active) renderCase();
  }
});
$("reject").onclick = async () => {
  if (!active || busy) return;
  busy = true;
  try {
    await api("/cases/" + active.id + "/decision", "POST", {
      version: active.version,
      approved: false,
    });
    dirty = false;
    active = await api("/cases/" + active.id);
    await loadCases();
  } catch (e) {
    error(e);
  } finally {
    busy = false;
    if (active) renderCase();
  }
};
for (const [id, path] of [
  ["regenerate", "regenerate"],
  ["reconcile", "reconcile"],
  ["refresh-replies", "refresh-replies"],
])
  $(id).onclick = async () => {
    if (!active || busy) return;
    busy = true;
    try {
      await api("/cases/" + active.id + "/" + path, "POST", {});
      dirty = false;
      active = await api("/cases/" + active.id);
      await loadCases();
    } catch (e) {
      error(e);
    } finally {
      busy = false;
      if (active) renderCase();
    }
  };
// Refresh only in-flight cases; never replace a reviewer's edits in the background.
setInterval(async () => {
  if (
    !workspace ||
    busy ||
    dirty ||
    !active ||
    !processing.includes(active.status) ||
    $("confirm").open ||
    $("recovery-dialog").open || $("audit-dialog").open || $("evidence-dialog").open
  )
    return;
  try {
    const selected = active.id, seq=reviewRequest;
    const result = await api("/cases/" + selected);
    if (active?.id!==selected || seq!==reviewRequest || dirty || busy || $("audit-dialog").open || $("evidence-dialog").open) return;
    active=result;
    renderCase();
    await loadCases();
  } catch (e) {
    error(e);
  }
}, 6000);
fetch("/health")
  .then((r) => r.json())
  .then((d) => {
    $("runtime").textContent = d.mode + " / " + (d.model || "Rules demo");
    $("version").textContent = "v" + d.version;
  })
  .catch(() => {
    $("runtime").textContent = "API unavailable";
  });
tab(location.hash.slice(1));
renderInbox();

// Recover only the displayed version. A second click cannot enqueue it twice.
let recoveryTarget = null;
$("recover").onclick = () => {
  if (!active || busy) return;
  recoveryTarget = { id: active.id, version: active.version };
  $("recovery-target").textContent =
    workspace.repo + " · #" + active.number + " · v" + active.version;
  $("recovery-actions").replaceChildren(
    ...active.actions
      .filter((a) => a.version === active.version)
      .map((a) =>
        node(
          "p",
          a.kind +
            " — " +
            a.status +
            ": " +
            (a.kind === "comment" ? a.payload.body : JSON.stringify(a.payload)),
        ),
      ),
  );
  $("ack-unknown").checked = false;
  $("recovery-mode").value = "resume";
  $("recovery-dialog").returnValue = "";
  $("recovery-dialog").showModal();
};
$("recovery-dialog").addEventListener("close", async () => {
  if ($("recovery-dialog").returnValue !== "recover" || !recoveryTarget || busy)
    return;
  const target = recoveryTarget;
  recoveryTarget = null;
  busy = true;
  try {
    const result = await api("/cases/" + target.id + "/recover", "POST", {
      version: target.version,
      mode: $("recovery-mode").value,
      acknowledge_unknown: $("ack-unknown").checked,
    });
    active = await api("/cases/" + target.id);
    dirty = false;
    await loadCases();
    tell(
      "Recovery recorded: " + result.state + ". Open this issue’s audit history for the outcome.",
    );
  } catch (e) {
    error(e);
  } finally {
    busy = false;
    if (active) renderCase();
  }
});
(async () => {
  let saved = null;
  try {
    saved = sessionStorage.getItem(sessionKey);
  } catch {}
  if (!saved) return;
  try {
    await connect(saved, true);
  } catch (e) {
    // A transient server outage must not erase a valid tab session.
    if (token)
      tell(
        "Session retained, but the server is unavailable. Use Refresh to reconnect.",
        true,
      );
    else error(e);
  }
})();

$("demo-reply").onclick = async () => {
  if (!active) return;
  const body = window.prompt("Synthetic reporter reply (for example Version: 1.95 and Reproduction: save draft)");
  if (!body) return;
  try { await api(`/cases/${active.id}/demo-reply`, "POST", {body}); active = await api(`/cases/${active.id}`); renderCase(); await loadCases(); } catch (e) { error(e); }
};
