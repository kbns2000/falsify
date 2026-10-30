/* Falsify front end — vanilla JS, no build step, SVG charts drawn by hand. */
(function () {
  "use strict";
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (x, d = 2, suf = "%") => (x == null || Number.isNaN(x) ? "n/a" : (Number(x) >= 0 && suf === "%" ? "+" : "") + Number(x).toFixed(d) + suf);
  const fmtN = (x, d = 2) => (x == null || Number.isNaN(x) ? "n/a" : Number(x).toFixed(d));
  const signCls = (x) => (x == null ? "" : x > 0 ? "pos" : x < 0 ? "neg" : "");

  const EXAMPLES = [
    "When bitcoin's RSI goes under 30 it always bounces within a week",
    "3일 연속 떨어지면 다음날 반등하니까 그때 산다",
    "Breakouts above the 20-day high on heavy volume keep running for two weeks on SOL",
    "Buying ETH every Monday open and selling at the close beats random days",
    "A golden cross (SMA20 over SMA50) on DOGE means a month of gains",
    "After a 10% drop in one day, XRP recovers over the next 3 days",
  ];

  const state = { meta: null, es: null, claimId: null, result: null, findings: [] };
  const searcher = (() => { try { let s = localStorage.getItem("falsify.searcher"); if (!s) { s = Math.random().toString(36).slice(2) + Date.now().toString(36); localStorage.setItem("falsify.searcher", s); } return s; } catch (e) { return "anon"; } })();

  // ------------------------------------------------------------ routing
  function navigate(path, push = true) {
    if (push) history.pushState({}, "", path);
    render(path);
  }
  function render(path) {
    const views = { "/": "view-home", "/ledger": "view-ledger" };
    const claim = path.match(/^\/claim\/([\w-]+)/);
    $$(".view").forEach((v) => v.classList.add("hidden"));
    if (claim) {
      $("#view-run").classList.remove("hidden");
      if (state.claimId !== claim[1]) openClaim(claim[1]);
    } else if (path.startsWith("/ledger")) {
      $("#view-ledger").classList.remove("hidden");
      loadLedger();
    } else {
      $("#view-home").classList.remove("hidden");
      loadScoreboard($("#scoreboard"));
    }
    $$("[data-nav]").forEach((a) => a.classList.toggle("current", a.getAttribute("data-nav") === path));
    window.scrollTo({ top: 0 });
  }
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[data-nav]");
    if (a) { e.preventDefault(); navigate(a.getAttribute("data-nav")); }
  });
  window.addEventListener("popstate", () => render(location.pathname));

  // ------------------------------------------------------------ meta
  async function loadMeta() {
    try {
      const m = await (await fetch("/api/meta")).json();
      state.meta = m;
      const b = $("#provider-badge");
      b.textContent = "model: " + (m.provider.offline ? "offline stub" : m.provider.label.replace("Open weights · ", ""));
      b.className = "badge " + (m.provider.offline ? "badge-warn" : "badge-ok");
      b.title = m.provider.label;
      const sel = $("#asset-hint");
      m.dataset.assets.forEach((a) => { const o = document.createElement("option"); o.value = a; o.textContent = a; sel.appendChild(o); });
      const ds = $("#dataset"); ds.innerHTML = "";
      (m.datasets || [m.dataset]).forEach((d) => { const o = document.createElement("option"); o.value = d.id; o.textContent = d.label + " · " + d.start + " → " + d.end; ds.appendChild(o); });
      ds.addEventListener("change", async () => { const r = await (await fetch("/api/assets?dataset_id=" + encodeURIComponent(ds.value))).json(); sel.innerHTML = '<option value="">auto (from the text)</option>'; r.assets.forEach((a) => { const o = document.createElement("option"); o.value = a; o.textContent = a; sel.appendChild(o); }); });
      $("#form-note").textContent = m.provider.offline ? "Offline mode: beliefs are mapped by simple patterns, not by a model. Set HF_TOKEN (open weights) or META_API_KEY on the server for the real thing." : "Dataset: " + m.dataset.label + " · " + m.dataset.start + " → " + m.dataset.end;
    } catch (e) { console.error(e); }
  }
  async function loadScoreboard(el) {
    try {
      const l = await (await fetch("/api/ledger?limit=1")).json();
      const by = l.scoreboard.by_label || {};
      const total = l.scoreboard.total || 0;
      const fals = by.FALSIFIED || 0, frag = by.FRAGILE || 0, surv = by.SURVIVED || 0, inc = by.INCONCLUSIVE || 0;
      el.innerHTML = [
        tile("Claims sealed", total, l.chain.ok ? "hash chain verified" : "chain broken"),
        tile("Falsified", fals, total ? Math.round((100 * fals) / Math.max(1, total - inc)) + "% of decided" : "—"),
        tile("Fragile", frag, "passed, then broke"),
        tile("Survived (for now)", surv, "not a forecast"),
        tile("Inconclusive", inc, "too few trades"),
      ].join("");
    } catch (e) { el.innerHTML = ""; }
  }
  const tile = (k, v, s) => `<div class="tile"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="s">${esc(s)}</div></div>`;

  // ------------------------------------------------------------ home form
  function initForm() {
    const ex = $("#examples");
    EXAMPLES.forEach((t) => { const c = document.createElement("button"); c.type = "button"; c.className = "chip"; c.textContent = t; c.onclick = () => { $("#belief").value = t; $("#belief").focus(); }; ex.appendChild(c); });
    $("#csv").addEventListener("change", uploadCsv);
    $("#claim-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const belief = $("#belief").value.trim();
      if (belief.length < 3) return;
      const btn = $("#submit"); btn.disabled = true; btn.textContent = "Sealing…";
      try {
        const r = await fetch("/api/claims", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ belief, dataset_id: $("#dataset").value, asset_hint: $("#asset-hint").value || null, searcher }) });
        if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
        const j = await r.json();
        resetRun(belief);
        state.claimId = j.id;
        navigate("/claim/" + j.id);
        listen(j.id);
      } catch (err) { $("#form-note").textContent = "Could not start: " + err.message; }
      finally { btn.disabled = false; btn.textContent = "Try to break it"; }
    });
  }
  async function uploadCsv(e) {
    const f = e.target.files[0]; if (!f) return;
    $("#form-note").textContent = "Uploading " + f.name + "…";
    const fd = new FormData(); fd.append("file", f); fd.append("asset", f.name.replace(/\.csv$/i, "").toUpperCase().slice(0, 20));
    try {
      const r = await fetch("/api/upload", { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "upload failed");
      const sel = $("#dataset"); const o = document.createElement("option"); o.value = j.id; o.textContent = j.label + " · " + j.timeframe + " · " + j.start + " → " + j.end; o.selected = true; sel.appendChild(o);
      const ah = $("#asset-hint"); ah.innerHTML = '<option value="">auto (from the text)</option>'; j.assets.forEach((a) => { const op = document.createElement("option"); op.value = a; op.textContent = a; ah.appendChild(op); }); if (j.assets.length === 1) ah.value = j.assets[0];
      $("#form-note").textContent = "Uploaded " + j.rows + " rows (" + j.assets.join(", ") + "). Your belief will be tested on this data.";
    } catch (err) { $("#form-note").textContent = "Upload failed: " + err.message; }
  }

  // ------------------------------------------------------------ run view
  function resetRun(belief) {
    state.result = null; state.findings = [];
    if (state.es) { state.es.close(); state.es = null; }
    $("#run-title").textContent = "Formalizing…";
    $("#run-belief").textContent = belief || "";
    $("#run-eyebrow").textContent = "Claim";
    $$(".stage").forEach((s) => { s.classList.remove("active", "done"); $(".stage-status", s).textContent = ""; });
    $$(".stage .stage-body").forEach((b) => { if (!b.querySelector("#findings") && !b.querySelector("#variants")) b.innerHTML = ""; });
    $("#findings").innerHTML = ""; $("#variants").innerHTML = "";
    setStage("understand", "active", "reading the belief");
  }
  function setStage(name, cls, status) {
    const s = $(`.stage[data-stage="${name}"]`); if (!s) return;
    s.classList.remove("active", "done"); if (cls) s.classList.add(cls);
    if (status != null) $(".stage-status", s).textContent = status;
  }
  const body = (name) => $(`.stage[data-stage="${name}"] .stage-body`);

  function listen(id) {
    const es = new EventSource(`/api/claims/${id}/events`);
    state.es = es;
    const on = (k, fn) => es.addEventListener(k, (ev) => { try { fn(JSON.parse(ev.data)); } catch (e) { console.error(k, e); } });
    on("understand.start", () => setStage("understand", "active", "reading the belief"));
    on("provider.fallback", (p) => { const b = $("#provider-badge"); b.textContent = "model route down → offline"; b.className = "badge badge-warn"; b.title = (p.error || ""); });
    on("formalize.attempt", (p) => setStage("understand", "active", "model attempt " + p.n));
    on("formalize.repair", (p) => setStage("understand", "active", "repairing: " + (p.reason || "").slice(0, 60)));
    on("understand.done", (p) => { renderSpec(p.spec, p.trace); setStage("understand", "done", (p.trace && p.trace.attempts ? p.trace.attempts.length : 1) + " attempt(s)"); setStage("seal", "active", "hashing"); });
    on("understand.error", (p) => { body("understand").innerHTML = `<div class="error"><b>Could not test this belief.</b> ${esc(p.message)}</div>`; setStage("understand", "done", "stopped"); $("#run-title").textContent = "Not testable"; });
    on("seal.done", (p) => { renderSeal(p); setStage("seal", "done", "sealed"); setStage("test", "active", "replaying"); });
    on("test.done", (p) => { renderTest(p.summary, p.equity_curve); setStage("test", "done", p.summary.n_trades + " trades"); setStage("attack", "active", "0 / 9"); });
    on("attack.finding", (p) => { state.findings.push(p.finding); addFinding(p.finding); setStage("attack", "active", state.findings.length + " / 9"); });
    on("attack.done", (p) => { setStage("attack", "done", (p.failed || []).length + " attack(s) landed"); setStage("advocate", "active", "asking the model"); });
    on("advocate.variant", (p) => addVariant(p.variant));
    on("advocate.done", (p) => { setStage("advocate", "done", p.n + " counter-test(s)"); setStage("verdict", "active", "deciding"); });
    on("verdict.done", (p) => { renderVerdict(p.verdict, null, null); setStage("verdict", "active", "writing the report"); });
    on("report.done", (p) => { renderVerdict(state.lastVerdict, p.report, p.provider); setStage("verdict", "done", "done"); });
    on("result", (r) => { state.result = r; if (r.error) { body("understand").innerHTML += `<div class="error">${esc(r.error.message || JSON.stringify(r.error))}</div>`; setStage("understand", "done", "stopped"); } else { renderAll(r); } });
    on("error", (p) => { body("verdict").innerHTML = `<div class="error">${esc(p.message)}</div>`; });
    on("__end__", () => { es.close(); state.es = null; });
    es.onerror = () => { /* the server closes after __end__; nothing to do */ };
  }

  async function openClaim(id) {
    state.claimId = id;
    resetRun("");
    $("#run-title").textContent = "Loading…";
    try {
      const r = await fetch(`/api/claims/${id}`);
      if (r.status === 202) { listen(id); return; }
      if (!r.ok) throw new Error("unknown claim");
      const res = await r.json();
      state.result = res;
      $("#run-belief").textContent = res.belief || "";
      if (res.error) { body("understand").innerHTML = `<div class="error">${esc(res.error.message || "")}</div>`; $("#run-title").textContent = "Not testable"; return; }
      renderAll(res);
    } catch (e) { $("#run-title").textContent = "Claim not found"; }
  }

  function renderAll(r) {
    $("#run-belief").textContent = r.belief || "";
    renderSpec(r.spec, r.trace); setStage("understand", "done", (r.trace && r.trace.attempts ? r.trace.attempts.length : 1) + " attempt(s)");
    renderSeal(r.seal); setStage("seal", "done", "sealed");
    renderTest(r.backtest, r.backtest.equity_curve); setStage("test", "done", r.backtest.n_trades + " trades");
    $("#findings").innerHTML = ""; state.findings = r.findings || []; state.findings.forEach(addFinding); setStage("attack", "done", (r.verdict.attacks_failed || []).length + " attack(s) landed");
    $("#variants").innerHTML = ""; (r.variants || []).forEach(addVariant); setStage("advocate", "done", (r.variants || []).length + " counter-test(s)");
    renderVerdict(r.verdict, r.report, r.provider); setStage("verdict", "done", "done");
    $("#run-eyebrow").textContent = "Claim " + r.id + (r.elapsed ? " · " + r.elapsed + "s" : "");
  }

  function renderSpec(spec, trace) {
    if (!spec) return;
    $("#run-title").textContent = spec.title || "Claim";
    const ex = spec.exit || {};
    const exitTxt = `hold ${ex.bars} bar${ex.bars > 1 ? "s" : ""}` + (ex.stop_pct ? ` · stop ${ex.stop_pct}%` : "") + (ex.target_pct ? ` · target ${ex.target_pct}%` : "") + (ex.exit_condition ? ` · or ${ex.exit_condition}` : "");
    const crit = spec.criterion || {};
    const critTxt = `${crit.metric === "win_rate" ? "win rate" : crit.metric === "total_net_return" ? "total return" : "mean net return"} must beat ${crit.beats === "random_entries" ? "random entries" : crit.beats === "zero" ? "zero" : "buy & hold"} (α ${crit.alpha}, ≥ ${crit.min_trades} trades)`;
    const assumptions = (trace && trace.assumptions) || [];
    body("understand").innerHTML = `
      <div class="rule">
        ${kv("Asset", spec.asset)}${kv("Direction", spec.direction)}${kv("Entry (at close)", spec.entry, true)}${kv("Exit", exitTxt)}${kv("Fees", spec.fee_bps_per_side + " bps per side")}
      </div>
      <div class="kv" style="margin-bottom:10px"><div class="k">Pre-registered test (frozen before any result)</div><div class="v">${esc(critTxt)}</div></div>
      <p class="plain">${esc(spec.plain_language || "")}</p>
      ${assumptions.length ? `<ul class="assume">${assumptions.map((a) => `<li>assumed: ${esc(a)}</li>`).join("")}</ul>` : ""}
      ${trace ? `<details><summary>Agent trace · ${esc(trace.provider || "")} · ${(trace.attempts || []).length} attempt(s)</summary><pre>${esc(JSON.stringify((trace.attempts || []).map((a) => ({ n: a.n, ok: a.ok, entry: a.entry, n_signals: a.n_signals, n_trades: a.n_trades, error: a.error, note: a.note })), null, 1))}</pre></details>` : ""}`;
  }
  const kv = (k, v, mono) => `<div class="kv"><div class="k">${esc(k)}</div><div class="v${mono ? " mono" : ""}">${esc(v)}</div></div>`;

  function renderSeal(s) {
    if (!s) return;
    body("seal").innerHTML = `<div class="hash">sealed <b>${esc(s.sealed_at)}</b> · claim #${(s.prior_claims_same_asset || 0) + 1} on this asset in the ledger<br>hash <b>${esc(s.spec_hash)}</b><br>prev ${esc(s.prev_hash || "(genesis)")}</div>
      <p class="plain" style="color:var(--muted);font-size:13px;margin-top:8px">The rule above is frozen. Everything below was computed after this moment, and the verdict will be written into the public ledger next to this hash.</p>`;
  }

  function renderTest(s, curve) {
    if (!s) return;
    const el = body("test");
    el.innerHTML = `<div class="metrics">
        ${kv("Signals → trades", `${s.n_signals ?? "–"} → ${s.n_trades}`)}
        ${kv("Mean per trade (net)", fmt(s.mean_net))}
        ${kv("Win rate", s.win_rate == null ? "n/a" : Math.round(100 * s.win_rate) + "%")}
        ${kv("Rule, compounded", fmt(s.total_net, 1))}
        ${kv("Just holding", fmt(s.buy_and_hold_total, 1))}
        ${kv("Max drawdown", s.max_drawdown == null ? "n/a" : fmtN(s.max_drawdown, 1) + "%")}
      </div><div id="eq"></div>
      <div class="legend"><span><i style="background:var(--accent)"></i>rule equity (1.0 = start)</span><span><i style="background:var(--info)"></i>buy &amp; hold</span></div>`;
    if (curve && curve.ts && curve.ts.length) lineChart($("#eq", el), curve);
  }

  function addFinding(f) {
    const li = document.createElement("li"); li.className = "finding";
    li.innerHTML = `<div class="row"><span class="st st-${esc(f.status)}">${esc(f.status).toUpperCase()}</span><div style="flex:1"><div class="name">${esc(f.name)}</div><div class="head">${esc(f.headline)}</div>${detailHtml(f)}</div></div>`;
    $("#findings").appendChild(li);
  }
  function detailHtml(f) {
    const d = f.details || {};
    if (f.id === "random_baseline" && d.hist) {
      const id = "h" + Math.random().toString(36).slice(2, 8);
      setTimeout(() => histogram($("#" + id), d.hist, d.rule_mean, d.random_mean), 0);
      return `<div id="${id}"></div><div class="legend"><span><i style="background:var(--muted)"></i>mean return of ${d.n_boot} random ${d.n_trades}-trade sets</span><span><i style="background:var(--accent)"></i>this rule</span></div>`;
    }
    if (f.id === "fees" && d.table) return table(["fee / side", "mean / trade", "total", "win rate"], d.table.map((r) => [r.fee_bps_per_side + " bps", c(r.mean_net), c(r.total_net, 1), Math.round(100 * r.win_rate) + "%"]));
    if (f.id === "time_split" && d.first_half) return table(["period", "trades", "mean / trade", "win rate"], [["first half → " + d.split_at, d.first_half.n, c(d.first_half.mean_net), pct(d.first_half.win_rate)], ["second half", d.second_half.n, c(d.second_half.mean_net), pct(d.second_half.win_rate)]].concat((d.per_year || []).map((y) => [y.year, y.n, c(y.mean_net), ""])));
    if (f.id === "neighbourhood" && d.table) return table(["variant", "trades", "mean / trade", "win rate"], d.table.map((r) => [r.variant, r.n_trades ?? "", c(r.mean_net), pct(r.win_rate)]));
    if (f.id === "cross_asset" && d.table) return table(["asset", "trades", "mean / trade", "excess vs random", "p"], d.table.map((r) => [r.asset, r.n_trades ?? "", c(r.mean_net), c(r.excess), r.p_value == null ? "" : fmtN(r.p_value, 3)]));
    return "";
  }
  const c = (x, d = 2) => (x == null ? "" : `<span class="${signCls(x)}">${fmt(x, d)}</span>`);
  const pct = (x) => (x == null ? "" : Math.round(100 * x) + "%");
  function table(head, rows) {
    return `<details><summary>details</summary><table class="mini"><thead><tr>${head.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((v, i) => `<td>${i === 0 ? esc(v) : v}</td>`).join("")}</tr>`).join("")}</tbody></table></details>`;
  }

  function addVariant(v) {
    const li = document.createElement("li"); li.className = "finding";
    const r = v.result;
    let res = "";
    if (!r) res = `<div class="head">not run</div>`;
    else if (r.error) res = `<div class="head">could not run: ${esc(r.error)}</div>`;
    else res = `<div class="head">${esc(r.asset)} · ${esc(r.entry)} · ${esc(r.direction)} · hold ${r.bars} → <b>${r.n_trades} trades, mean ${c(r.mean_net)}</b>${r.p_value != null ? ` · p vs random ${fmtN(r.p_value, 3)}` : ""}</div>`;
    li.innerHTML = `<div class="row"><span class="st st-info">TEST</span><div style="flex:1"><div class="name">${esc(v.label)}</div><div class="head">${esc(v.why || "")}</div>${res}</div></div>`;
    $("#variants").appendChild(li);
  }

  function renderVerdict(v, report, provider) {
    if (!v) return;
    state.lastVerdict = v;
    body("verdict").innerHTML = `<div class="verdict-card ${esc(v.label)}">
        <div class="verdict-label">${esc(v.label)}${v.label === "SURVIVED" ? " <span style='font-size:16px;font-weight:600'>(for now)</span>" : ""}</div>
        <div class="verdict-sub">${esc(v.subtitle)}${v.p_value != null ? ` · p vs random = ${fmtN(v.p_value, 3)}` : ""}</div>
        <ul class="reasons">${(v.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}${(v.warnings || []).map((w) => `<li style="color:var(--muted)">also: ${esc(w)}</li>`).join("")}</ul>
        ${report ? `<div class="report">${md(report)}</div>` : `<div class="report" style="color:var(--muted)">writing the report…</div>`}
        ${provider ? `<div class="usage">${esc(provider.label)} · ${provider.calls} call(s) · ${provider.prompt_tokens}+${provider.completion_tokens} tokens · ${provider.seconds}s</div>` : ""}
        <p style="color:var(--muted);font-size:12px;margin:10px 0 0">${esc(v.disclaimer || "")}</p>
      </div>`;
  }

  // minimal markdown: **bold** headers, bullets, paragraphs
  function md(text) {
    const lines = String(text).split(/\r?\n/);
    let html = "", inList = false;
    const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
    for (const raw of lines) {
      const l = raw.trim();
      if (!l) { if (inList) { html += "</ul>"; inList = false; } continue; }
      if (/^[-*•]\s+/.test(l)) { if (!inList) { html += "<ul>"; inList = true; } html += `<li>${inline(l.replace(/^[-*•]\s+/, ""))}</li>`; continue; }
      if (inList) { html += "</ul>"; inList = false; }
      if (/^#{1,4}\s/.test(l)) { html += `<h4>${inline(l.replace(/^#+\s/, ""))}</h4>`; continue; }
      if (/^\*\*[^*]+\*\*:?$/.test(l)) { html += `<h4>${inline(l.replace(/\*\*/g, "").replace(/:$/, ""))}</h4>`; continue; }
      html += `<p>${inline(l)}</p>`;
    }
    if (inList) html += "</ul>";
    return html;
  }

  // ------------------------------------------------------------ charts (SVG)
  function lineChart(el, curve) {
    const W = 920, H = 260, P = { l: 48, r: 12, t: 10, b: 28 };
    const n = curve.ts.length;
    const all = curve.rule.concat(curve.hold);
    let lo = Math.min(...all), hi = Math.max(...all);
    if (hi - lo < 1e-9) { hi += 0.05; lo -= 0.05; }
    const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
    const x = (i) => P.l + (i / Math.max(1, n - 1)) * (W - P.l - P.r);
    const y = (v) => P.t + (1 - (v - lo) / (hi - lo)) * (H - P.t - P.b);
    const path = (arr) => arr.map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1)).join(" ");
    const ticks = 4; let grid = "";
    for (let i = 0; i <= ticks; i++) { const v = lo + ((hi - lo) * i) / ticks; grid += `<line class="grid" x1="${P.l}" x2="${W - P.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${P.l - 6}" y="${y(v) + 4}" text-anchor="end">${v.toFixed(2)}</text>`; }
    let xl = ""; const step = Math.max(1, Math.floor(n / 6));
    for (let i = 0; i < n; i += step) xl += `<text x="${x(i)}" y="${H - 8}" text-anchor="middle">${esc(curve.ts[i])}</text>`;
    el.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Equity curve">${grid}<line class="axis" x1="${P.l}" x2="${W - P.r}" y1="${y(1)}" y2="${y(1)}" stroke-dasharray="3 3"/>${xl}<path d="${path(curve.hold)}" fill="none" stroke="var(--info)" stroke-width="1.6" opacity=".9"/><path d="${path(curve.rule)}" fill="none" stroke="var(--accent)" stroke-width="2.2"/></svg>`;
  }
  function histogram(el, hist, ruleMean, randomMean) {
    if (!el) return;
    const W = 920, H = 200, P = { l: 40, r: 12, t: 12, b: 28 };
    const counts = hist.counts, edges = hist.edges;
    const lo = Math.min(edges[0], ruleMean), hi = Math.max(edges[edges.length - 1], ruleMean);
    const span = (hi - lo) || 1;
    const x = (v) => P.l + ((v - lo) / span) * (W - P.l - P.r);
    const maxC = Math.max(...counts, 1);
    const y = (c) => P.t + (1 - c / maxC) * (H - P.t - P.b);
    let bars = "";
    counts.forEach((cnt, i) => { const x0 = x(edges[i]), x1 = x(edges[i + 1]); bars += `<rect x="${x0 + 0.5}" y="${y(cnt)}" width="${Math.max(1, x1 - x0 - 1)}" height="${H - P.b - y(cnt)}" fill="var(--muted)" opacity=".55"/>`; });
    let xl = ""; for (let i = 0; i <= 5; i++) { const v = lo + (span * i) / 5; xl += `<text x="${x(v)}" y="${H - 8}" text-anchor="middle">${v.toFixed(2)}%</text>`; }
    const rx = x(ruleMean), mx = x(randomMean);
    el.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Random baseline">${bars}${xl}<line x1="${mx}" x2="${mx}" y1="${P.t}" y2="${H - P.b}" stroke="var(--muted)" stroke-dasharray="4 3"/><line x1="${rx}" x2="${rx}" y1="${P.t}" y2="${H - P.b}" stroke="var(--accent)" stroke-width="2.5"/><text x="${rx + 6}" y="${P.t + 12}" fill="var(--accent)">rule ${ruleMean.toFixed(2)}%</text><text x="${mx + 6}" y="${H - P.b - 6}">random mean ${randomMean.toFixed(2)}%</text></svg>`;
  }

  // ------------------------------------------------------------ ledger
  async function loadLedger() {
    try {
      const l = await (await fetch("/api/ledger?limit=200")).json();
      loadScoreboard($("#scoreboard-ledger"));
      $("#chain-status").innerHTML = l.chain.ok ? `<span class="badge badge-ok">chain verified · ${l.chain.entries} entries</span>` : `<span class="badge badge-warn">chain broken at ${esc(l.chain.broken_at)}</span>`;
      const tb = $("#ledger-table tbody");
      tb.innerHTML = l.entries.map((e) => {
        const v = e.verdict || {};
        return `<tr><td>${esc((e.sealed_at || "").replace("T", " ").replace("+00:00", "").slice(0, 16))}</td><td><a href="/claim/${esc(e.id)}" data-nav="/claim/${esc(e.id)}"><b>${esc(e.title)}</b></a><div style="color:var(--muted);font-size:12px;max-width:320px;white-space:normal">${esc(e.belief)}</div></td><td>${esc(e.asset)}</td><td class="mono" title="${esc(e.entry)}">${esc(e.entry)} · ${e.bars}b ${esc(e.direction)}</td><td>${v.n_trades ?? ""}</td><td>${v.mean_net == null ? "" : c(v.mean_net)}</td><td><span class="vb ${esc(v.label || "PENDING")}">${esc(v.label || "PENDING")}</span></td><td class="mono" title="${esc(e.spec_hash)}">${esc((e.spec_hash || "").slice(0, 8))}…</td></tr>`;
      }).join("") || `<tr><td colspan="8" style="color:var(--muted)">No claims yet. Seal the first one.</td></tr>`;
    } catch (e) { console.error(e); }
  }

  // ------------------------------------------------------------ boot
  $("#copy-link").addEventListener("click", () => { navigator.clipboard && navigator.clipboard.writeText(location.href); $("#copy-link").textContent = "Copied"; setTimeout(() => ($("#copy-link").textContent = "Copy link"), 1200); });
  initForm();
  loadMeta().then(() => render(location.pathname));
})();
