"use strict";
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (t) => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, "0")}`;
const fmtShort = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}`;
let api = null, S = { jobs: [] }, sel = null;
let centerSig = "", inspSig = "", review = null, pending = null, tab = "ending", selRow = null, editing = null;

// caption-style text: words of the clip's own look, with the active word lit
function cap(text, on = -1, cls = "") {
  return `<div class="cap ${cls}">${text.split(" ").map((w, i) => `<span class="${i === on ? "on" : ""}">${esc(w)}</span>`).join(" ")}</div>`;
}

// ---------------------------------------------------------------- polling
async function tick() {
  try { S = await api.state(); } catch (e) { return; }
  if (sel && !S.jobs.some((j) => j.key === sel)) sel = null;
  if (!sel && S.jobs.length) sel = (S.jobs.find((j) => j.status === "Working") || S.jobs[S.jobs.length - 1]).key;
  const e = $("#engine");
  e.textContent = S.engine.label;
  e.classList.toggle("cpu", !S.engine.gpu);
  renderQueue();
  const job = S.jobs.find((j) => j.key === sel) || null;
  renderCenter(job);
  renderInspector(job);
}

// ---------------------------------------------------------------- queue
function jobLine(j) {
  if (j.status === "Working") return [j.stageText, `${j.pct}%`];
  if (j.status === "Done") return [j.result.problems?.length ? "Ready, check glitches" : "Ready", fmtShort(j.result.end)];
  if (j.status === "Waiting") return ["In line", ""];
  if (j.status === "Cancelled") return ["Stopped", ""];
  return ["Failed", "Retry"];
}
function renderQueue() {
  const n = S.jobs.length, done = S.jobs.filter((j) => j.status === "Done").length;
  $("#count").textContent = n ? `${done} of ${n} ready` : "";
  $("#jobs").innerHTML = S.jobs.map((j) => {
    const [a, b] = jobLine(j);
    const cls = { Working: "working", Done: "done", Failed: "failed", Cancelled: "failed" }[j.status] || "";
    return `<li class="job ${cls} ${j.key === sel ? "sel" : ""}" data-key="${j.key}" tabindex="0">
      <div class="name" title="${esc(j.name)}">${esc(j.name)}</div>
      <div class="sub"><span>${esc(a)}</span><b>${esc(b)}</b></div>
      ${j.status === "Working" ? `<div class="bar"><i style="width:${j.pct}%"></i></div>` : ""}
      ${j.status !== "Working" ? `<button class="x" data-remove="${j.key}" title="Remove from list" aria-label="Remove">✕</button>` : ""}
    </li>`;
  }).join("");
}
$("#jobs").addEventListener("click", async (e) => {
  const rm = e.target.closest("[data-remove]");
  if (rm) { e.stopPropagation(); await api.remove(rm.dataset.remove); return tick(); }
  const li = e.target.closest(".job");
  if (li) { sel = li.dataset.key; tick(); }
});
$("#jobs").addEventListener("keydown", (e) => { if (e.key === "Enter") e.target.closest(".job")?.click(); });

// ---------------------------------------------------------------- center monitor
let idleTimer = null;
function renderCenter(job) {
  const sig = job ? `${job.key}|${job.status}|${job.video || ""}|${job.stage}` : "none";
  const screen = $("#screen"), under = $("#under");
  $("#halo").classList.toggle("working", job?.status === "Working");
  if (sig !== centerSig) {
    centerSig = sig;
    clearInterval(idleTimer);
    if (!job) {
      screen.innerHTML = `<div class="idle-top">${cap("Drop a video", 0)}</div>
        <div class="screen-note" style="margin-top:46%">Talk-show clip in, vertical captioned clip out.</div>`;
      let k = 0;
      idleTimer = setInterval(() => {
        const spans = screen.querySelectorAll(".cap span");
        spans.forEach((s, i) => s.classList.toggle("on", i === k % spans.length));
        k++;
      }, 520);
      under.innerHTML = `<div class="under-actions"><button class="btn primary" data-act="add">Add videos</button></div>`;
    } else if (job.status === "Done") {
      screen.innerHTML = `<video id="vid" src="${job.video}" controls preload="metadata" playsinline></video>`;
      under.innerHTML = `<div class="under-actions">
          <button class="btn" data-act="open">Open in player</button>
          <button class="btn" data-act="reveal">Show in folder</button></div>`;
    } else if (job.status === "Working") {
      screen.innerHTML = `<div class="scan"></div><div class="bars">${"<i></i>".repeat(18)}</div>
        <div class="big" id="bigpct">0<small>%</small></div><div id="stagecap" style="margin-top:14px"></div>
        <div class="screen-note" id="stagenote"></div>`;
      screen.querySelectorAll(".bars i").forEach((b, i) => (b.style.animationDelay = `${(i * 0.13) % 1.1}s`));
      under.innerHTML = `<div id="track"></div><div class="under-actions" style="margin-top:16px">
          <button class="btn ghost small" data-act="cancel">Stop</button></div>`;
    } else if (job.status === "Waiting") {
      screen.innerHTML = cap("In line", 1) + `<div class="screen-note">Starts when the video before it is done.</div>`;
      under.innerHTML = "";
    } else {
      screen.innerHTML = cap(job.status === "Cancelled" ? "Stopped" : "Didn't finish", -1, "small") +
        `<div class="screen-note">${esc(job.error || "Stopped before it finished.")}</div>`;
      under.innerHTML = `<div class="under-actions"><button class="btn primary" data-act="retry">Try again</button></div>`;
    }
  }
  if (job?.status === "Working") updateWorking(job);
}
function updateWorking(job) {
  const p = job.parts || {};
  $("#bigpct").innerHTML = `${job.pct}<small>%</small>`;
  const words = { model: "Getting model", gpupack: "Getting GPU pack", verify: "Checking", work: p.render !== undefined ? "Rendering" : "Listening" }[job.stage] || "Starting";
  $("#stagecap").innerHTML = cap(words, words.split(" ").length - 1, "small");
  $("#stagenote").textContent = job.stage === "model" ? "First video only: downloading the speech model (about 1.6 GB)."
    : job.stage === "gpupack" ? "First video only: downloading the NVIDIA speed-up pack (about 1 GB)." : "";
  let html;
  if (job.stage === "model" || job.stage === "gpupack") {
    const f = p[job.stage] || 0;
    html = `<div class="step active"><div class="rail"><i style="width:${f * 100}%"></i></div>
      <b>${job.stage === "model" ? "Speech model download" : "GPU pack download"}</b><span>${Math.round(f * 100)}%</span></div>`;
  } else {
    const steps = [["Transcribe", p.transcribe], ["Find faces", p.analyze], ["Render", p.render], ["Check", job.stage === "verify" ? 0.5 : undefined]];
    html = `<div class="track">${steps.map(([name, f], i) => {
      const done = (f ?? 0) >= 1 || (i < 3 && job.stage === "verify");
      const active = !done && f !== undefined;
      const w = done ? 100 : Math.round((f || 0) * 100);
      return `<div class="step ${done ? "done" : active ? "active" : ""}"><div class="rail"><i style="width:${w}%"></i></div>
        <b>${name}</b><span>${done ? "Done" : active ? w + "%" : "Waiting"}</span></div>`;
    }).join("")}</div>`;
  }
  $("#track").innerHTML = html;
}
$("#under").addEventListener("click", async (e) => {
  const a = e.target.closest("[data-act]")?.dataset.act;
  if (!a) return;
  if (a === "add") await api.add_dialog();
  if (a === "open") await api.open_file(sel);
  if (a === "reveal") await api.reveal(sel);
  if (a === "cancel") await api.cancel();
  if (a === "retry") await api.retry(sel);
  tick();
});

// ---------------------------------------------------------------- inspector
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
function effectiveEnd() {
  if (pending.end) return pending.end;
  if (pending.keep_outro) return review.result.duration;
  return review.result.end;
}
function endReason(r) {
  return { ad: `Cut just before the ad that starts at ${fmt(r.ad_start || 0)}.`, logo: "End logo screen trimmed.",
    manual: "Ending set by you.", full: "Nothing needed trimming at the end." }[r.end_reason];
}

async function renderInspector(job) {
  const el = $("#inspector");
  const sig = job ? `${job.key}|${job.status}|${job.result ? job.result.end + job.result.out : ""}` : "none";
  if (sig === inspSig) {
    if (job && job.status !== "Done" && tab === "log") refreshLog(job);
    return;
  }
  inspSig = sig; selRow = null; editing = null;
  if (!job) {
    el.innerHTML = `<div class="empty-insp"><h3>How it works</h3>
      Add one or more talk-show videos. Each one becomes a 1080×1920 clip saved next to the original:
      <ul><li>the person talking stays centered</li><li>captions light up word by word</li>
      <li>a waveform card fills moments with nobody on screen</li><li>the ad or outro at the end is cut off</li></ul>
      Afterwards you can move the ending or fix a misheard word here.</div>`;
    return;
  }
  if (job.status !== "Done") {
    tab = "log";
    el.innerHTML = `<h2 class="insp-title">${esc(job.name)}</h2>
      <div class="insp-meta">${job.status === "Waiting" ? "Waiting for its turn." : job.status === "Working" ? "Working on it. You can keep adding videos." : esc(job.error || "")}</div>
      <div class="tabs"><button class="tab on">Log</button></div><pre class="log" id="log"></pre>`;
    refreshLog(job);
    return;
  }
  review = await api.review(job.key, null);
  if (!review) { el.innerHTML = `<div class="empty-insp">This clip has no transcript to review.</div>`; return; }
  pending = JSON.parse(JSON.stringify(review.edits));
  if (tab === "log") tab = "ending";
  drawInspector(job);
}

function drawInspector(job) {
  const r = review.result, el = $("#inspector");
  el.innerHTML = `<h2 class="insp-title" title="${esc(job.name)}">${esc(job.name)}</h2>
    <div class="insp-meta"><em>${fmt(r.end)}</em> of ${fmt(r.duration)} kept. ${esc(endReason(r))}
      ${r.device === "cuda" ? "Rendered on the GPU." : "Rendered on the CPU."}</div>
    ${r.problems?.length ? `<div class="warn">Possible glitch frames at ${r.problems.slice(0, 6).map(fmt).join(", ")}. Play those moments before posting.</div>` : ""}
    <div class="tabs" role="tablist">
      <button class="tab ${tab === "ending" ? "on" : ""}" data-tab="ending">Ending</button>
      <button class="tab ${tab === "captions" ? "on" : ""}" data-tab="captions">Captions</button>
      <button class="tab ${tab === "log" ? "on" : ""}" data-tab="log">Log</button></div>
    <div id="pane" style="display:flex;flex-direction:column;flex:1;min-height:0"></div>
    <div class="foot"><span class="pending" id="pendingTxt"></span>
      <button class="btn primary" id="apply" disabled>Re-render</button></div>`;
  drawPane(job);
  updatePending();
}

function drawPane(job) {
  const pane = $("#pane");
  if (tab === "log") { pane.innerHTML = `<pre class="log" id="log"></pre>`; refreshLog(job); return; }
  const sn = review.sentences, dur = review.result.duration;
  if (tab === "ending") {
    const end = effectiveEnd();
    const tail = sn.map((s, k) => [s, k]).filter(([s]) => s.e > dur - 120);
    let html = "", drawn = false;
    const cutHtml = `<div class="cutline" id="cutline">Clip ends at ${fmt(end)}</div>`;
    for (const [s, k] of tail) {
      if (!drawn && s.s >= end - 0.01) { html += cutHtml; drawn = true; }
      html += `<div class="row ${drawn ? "cut" : ""} ${selRow === k ? "sel" : ""}" data-k="${k}">
        <span class="t">${fmt(s.s)}</span><span>${esc(s.text)}</span>
        ${selRow === k ? `<div class="act"><button class="btn small" data-endafter="${k}">End the clip after this line</button></div>` : ""}</div>`;
    }
    if (!drawn) html += cutHtml;
    const mode = pending.end ? "manual" : pending.keep_outro ? "keep" : "auto";
    pane.innerHTML = `<div class="hint">The last two minutes. Click a line to jump there in the clip; lines under the yellow line are cut.</div>
      <div class="list" id="list">${html}</div>
      <div class="modes">
        <button class="btn small ${mode === "auto" ? "" : "ghost"}" data-mode="auto">Find the ending automatically</button>
        <button class="btn small ${mode === "keep" ? "" : "ghost"}" data-mode="keep">Keep everything</button></div>`;
    const cl = $("#cutline");
    if (cl && selRow === null) cl.scrollIntoView({ block: "center" });
  } else {
    pane.innerHTML = `<div class="hint">Click a line to fix a misheard word, like a name. Swear words are masked on screen automatically.</div>
      <div class="list" id="list">${sn.map((s, k) => `<div class="row ${s.fixed ? "fixed" : ""}" data-k="${k}">
        <span class="t">${fmt(s.s)}</span><span class="txt">${esc(s.text)}</span></div>`).join("")}</div>`;
  }
}

async function refreshLog(job) {
  const el = $("#log");
  if (!el) return;
  const atBottom = el.scrollTop + el.clientHeight >= el.scrollHeight - 20;
  el.textContent = await api.log(job.key);
  if (atBottom) el.scrollTop = el.scrollHeight;
}

function updatePending() {
  const ch = [];
  if (pending.end !== review.edits.end || !!pending.keep_outro !== !!review.edits.keep_outro) ch.push("new ending");
  const nf = (pending.words || []).length - (review.edits.words || []).length;
  if (!same(pending.words || [], review.edits.words || [])) ch.push("caption fixes");
  $("#pendingTxt").textContent = ch.length ? `Changed: ${ch.join(" and ")}` : "";
  $("#apply").disabled = !ch.length;
}

$("#inspector").addEventListener("click", async (e) => {
  const job = S.jobs.find((j) => j.key === sel);
  if (!job || !review) return;
  const t = e.target.closest("[data-tab]");
  if (t) { tab = t.dataset.tab; selRow = null; editing = null; return drawInspector(job); }
  if (e.target.id === "apply") { await api.apply(job.key, pending); inspSig = ""; return tick(); }
  const m = e.target.closest("[data-mode]");
  if (m) {
    pending.end = null; pending.keep_outro = m.dataset.mode === "keep";
    drawPane(job); return updatePending();
  }
  const ea = e.target.closest("[data-endafter]");
  if (ea) {
    const k = +ea.dataset.endafter, sn = review.sentences;
    const nxt = sn[k + 1] ? sn[k + 1].s : null;
    pending.end = Math.round((nxt ? Math.min(sn[k].e + 0.3, nxt - 0.05) : sn[k].e + 0.3) * 100) / 100;
    pending.keep_outro = false; selRow = null;
    drawPane(job); return updatePending();
  }
  const row = e.target.closest(".row");
  if (!row || e.target.tagName === "INPUT") return;
  const k = +row.dataset.k;
  if (tab === "ending") {
    selRow = selRow === k ? null : k;
    const s = review.sentences[k], v = $("#vid");
    if (v && s.s < review.result.end) { v.currentTime = Math.max(0, s.s - 0.2); v.play().catch(() => {}); }
    const top = $("#list").scrollTop;
    drawPane(job);
    $("#list").scrollTop = top;
  } else if (tab === "captions" && editing === null) {
    editing = k;
    const txt = row.querySelector(".txt");
    const input = document.createElement("input");
    input.value = review.sentences[k].text;
    txt.replaceWith(input);
    input.focus();
    const done = async (save) => {
      if (editing !== k) return;
      editing = null;
      if (save && input.value.trim() !== review.sentences[k].text) {
        const res = await api.fix_caption(job.key, pending, k, input.value.trim());
        pending = res.edits; review.sentences = res.sentences;
      }
      const top = $("#list").scrollTop;
      drawPane(job); updatePending();
      $("#list").scrollTop = top;
    };
    input.addEventListener("keydown", (ev) => { if (ev.key === "Enter") done(true); if (ev.key === "Escape") done(false); });
    input.addEventListener("blur", () => done(true));
  }
});

// ---------------------------------------------------------------- adding videos
$("#add").addEventListener("click", async () => { await api.add_dialog(); tick(); });
$("#drop").addEventListener("click", async () => { await api.add_dialog(); tick(); });
let dragDepth = 0;
document.addEventListener("dragenter", (e) => { e.preventDefault(); dragDepth++; document.body.classList.add("dragging"); });
document.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dragging"); } });
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", () => { dragDepth = 0; document.body.classList.remove("dragging"); setTimeout(tick, 300); });

window.addEventListener("pywebviewready", () => {
  api = window.pywebview.api;
  tick();
  setInterval(tick, 500);
});
