// Demo mode: the fixed key below opens the whole site with sample clips, all inside the browser. Nothing is sent to
// the API, nothing is uploaded and no GPU runs, so the key is safe to share for trying out the UI.
import type { Edits, Invite, Job, Result, Sentence, User } from "./api";

export const DEMO_KEY = (process.env.NEXT_PUBLIC_DEMO_KEY || "REELUP").toUpperCase();
export const DEMO_TOKEN = "demo";
export const DEMO_USER: User = { id: "demo", name: "Demo", admin: true };
const KEY = "fbclip.demo";
const RENDER_SECONDS = 40;

const LINES = [
  "So when you first got to the city, what was that like?", "Man, it was crazy, I didn't know nobody.",
  "I was sleeping on my cousin's couch for like six months.", "And then bruh manager called me up out of nowhere.",
  "He said, yo, I heard the mixtape, come through.", "That's when everything changed for real.",
  "We appreciate you for pulling up today.", "Thank you for having me, it's all love.",
  "Call Top Dog Law today if you've been injured in an accident.", "Go to topdoglaw.com, that's topdoglaw.com.",
  "Hold up. Every day I wake up. The Breakfast Club.",
];
const SENTENCES: Sentence[] = (() => {
  let t = 120;
  return LINES.map((text) => {
    const s = t, e = t + text.split(" ").length * 0.34;
    t = e + 1.3;
    return { s: +s.toFixed(2), e: +e.toFixed(2), text, fixed: false };
  });
})();
const AD = SENTENCES[8].s, DURATION = +(SENTENCES[10].e + 4).toFixed(2);
const blank: Edits = { end: null, keep_outro: false, words: [], wave: [], face: [] };

type Run = { started: number; edits: Edits };
type State = { jobs: Job[]; runs: Record<string, Run>; invites: Invite[] };

function result(edits: Edits, at: number): Result {
  const end = edits.end ?? (edits.keep_outro ? DURATION : +(AD - 0.4).toFixed(2));
  return { duration: DURATION, end, end_reason: edits.end != null ? "manual" : edits.keep_outro ? "full" : "ad",
    ad_start: AD, encoder: "h264_nvenc", device: "cuda", problems: [], size: Math.round(end * 1.15e6), rendered_at: at, edits };
}

function job(p: Partial<Job> & { id: string; name: string }): Job {
  const now = Date.now() / 1000;
  return { status: "done", pct: 100, stage_text: "", error: null, created: now - 3600, updated: now, owner_name: "Demo",
    renders: 1, edits: blank, result: null, log_tail: [], source: { kind: "upload" }, video_url: null, download_url: null, ...p };
}

function seed(): State {
  const now = Date.now() / 1000;
  const done = (id: string, name: string, ago: number, extra: Partial<Job> = {}) =>
    job({ id, name, created: now - ago, result: result(blank, now - ago + 240), video_url: "demo", download_url: "#demo", ...extra });
  return {
    jobs: [
      job({ id: "demo-live", name: "Marlon Wayans full interview", status: "running", pct: 0, created: now - 60 }),
      done("demo-done", "Saucy Santana on The Breakfast Club", 2400),
      done("demo-link", "youtube.com/watch?v=dQw4", 7200, { source: { kind: "link", url: "https://youtube.com/watch?v=dQw4" } }),
      job({ id: "demo-fail", name: "phone recording.mov", status: "failed", pct: 12, created: now - 86400,
        error: "The video is 1080×1920 (portrait). Upload a landscape 16:9 video.",
        log_tail: ["probe: 1080x1920 29.97fps 4:12", "error: not 16:9"] }),
    ],
    runs: { "demo-live": { started: now, edits: blank } },
    invites: [
      { id: "u1", name: "Jordan", code: "FB-7KQ2-MX4P", created: now - 86400 * 3, revoked: false, jobs: 6 },
      { id: "u2", name: "Sam", code: "FB-R9TD-2HWE", created: now - 86400 * 9, revoked: true, jobs: 1 },
    ],
  };
}

function load(): State {
  try { const s = localStorage.getItem(KEY); if (s) return JSON.parse(s); } catch { /* fresh */ }
  return seed();
}
function save(s: State) {
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* private mode */ }
}
export function resetDemo() {
  try { localStorage.removeItem(KEY); } catch { /* ignore */ }
}

const STAGES: [number, string][] = [[0, "Waiting for a GPU"], [8, "Transcribing"], [35, "Finding the speaker"], [60, "Rendering"], [92, "Checking frames"]];

/** advance simulated renders by wall-clock time */
function tick(s: State) {
  const now = Date.now() / 1000;
  for (const j of s.jobs) {
    const run = s.runs[j.id];
    if (!run || !["queued", "fetching", "running"].includes(j.status)) continue;
    const pct = Math.min(100, Math.round(((now - run.started) / RENDER_SECONDS) * 100));
    if (pct >= 100) {
      j.status = "done"; j.pct = 100; j.stage_text = ""; j.edits = run.edits;
      j.result = result(run.edits, now); j.video_url = "demo"; j.download_url = "#demo";
      delete s.runs[j.id];
    } else {
      j.status = pct < 8 ? "queued" : "running"; j.pct = pct;
      j.stage_text = [...STAGES].reverse().find(([p]) => pct >= p)![1];
    }
    j.updated = now;
  }
}

function sentences(edits: Edits): Sentence[] {
  return SENTENCES.map((s) => {
    const w = edits.words.find((x) => x.at >= s.s - 0.01 && x.at <= s.e);
    return w ? { ...s, text: w.to, fixed: true } : s;
  });
}

function fail(status: number, msg: string): never {
  throw Object.assign(new Error(msg), { status });
}

export async function demoApi(path: string, method: string, body: Record<string, unknown> | undefined): Promise<unknown> {
  await new Promise((r) => setTimeout(r, 150));
  const s = load();
  tick(s);
  const now = Date.now() / 1000;
  const m = path.match(/^\/jobs\/([^/]+)(?:\/(\w+))?$/);
  const j = m ? s.jobs.find((x) => x.id === m[1]) : undefined;
  let out: unknown;

  if (path === "/jobs" && method === "GET") out = s.jobs.filter((x) => x.status !== "uploading");
  else if (path === "/jobs/upload" || path === "/jobs/link") {
    if (s.jobs.filter((x) => ["queued", "fetching", "running"].includes(x.status)).length >= 3) fail(429, "You already have 3 clips in progress. Wait for one to finish.");
    const name = path === "/jobs/link" ? String(body?.url).replace(/^https?:\/\/(www\.)?/, "") : String(body?.filename);
    const nj = job({ id: "demo-" + Math.random().toString(36).slice(2, 8), name, status: path === "/jobs/link" ? "fetching" : "uploading",
      pct: 0, created: now, source: path === "/jobs/link" ? { kind: "link", url: String(body?.url) } : { kind: "upload", filename: name } });
    s.jobs.unshift(nj);
    if (path === "/jobs/link") s.runs[nj.id] = { started: now, edits: blank };
    out = path === "/jobs/link" ? nj : { job: nj, upload_url: "demo:" + nj.id };
  } else if (path === "/admin/invites" && method === "GET") out = s.invites;
  else if (path === "/admin/invites") {
    const code = "FB-" + Array.from({ length: 8 }, () => "ABCDEFGHJKMNPQRSTUVWXYZ23456789"[Math.floor(Math.random() * 31)]).join("").replace(/^(.{4})/, "$1-");
    const inv = { id: "u" + Date.now(), name: String(body?.name), code, created: now, revoked: false, jobs: 0 };
    s.invites.unshift(inv);
    out = inv;
  } else if (path.startsWith("/admin/invites/")) {
    const inv = s.invites.find((x) => x.id === path.split("/").pop());
    if (inv) inv.revoked = true;
    out = { ok: true };
  } else if (m) {
    if (!j) fail(404, "Clip not found.");
    const act = m[2];
    if (!act && method === "DELETE") { s.jobs = s.jobs.filter((x) => x !== j); delete s.runs[j.id]; out = { ok: true }; }
    else if (!act) out = j;
    else if (act === "start" || act === "retry") {
      Object.assign(j, { status: "queued", pct: 0, error: null, log_tail: [] });
      s.runs[j.id] = { started: now, edits: j.edits };
      out = j;
    } else if (act === "cancel") { j.status = "cancelled"; delete s.runs[j.id]; out = j; }
    else if (act === "review") out = { result: j.result, edits: j.edits, sentences: sentences(j.edits) };
    else if (act === "preview") out = { sentences: sentences(body?.edits as Edits) };
    else if (act === "fix") {
      const e = body?.edits as Edits, si = Number(body?.si), sent = SENTENCES[si];
      const words = e.words.filter((w) => w.at < sent.s - 0.01 || w.at > sent.e).concat({ at: sent.s, to: String(body?.text) });
      out = { edits: { ...e, words }, sentences: sentences({ ...e, words }) };
    } else if (act === "apply") {
      Object.assign(j, { status: "queued", pct: 0, renders: j.renders + 1 });
      s.runs[j.id] = { started: now - RENDER_SECONDS * 0.6, edits: body?.edits as Edits };
      out = j;
    }
  } else fail(404, "Not available in demo mode.");

  save(s);
  return JSON.parse(JSON.stringify(out));
}

/** pretend upload: progress over ~3 s */
export function demoPut(onProgress: (f: number) => void) {
  return new Promise<void>((resolve) => {
    let f = 0;
    const t = setInterval(() => {
      f = Math.min(1, f + 0.08);
      onProgress(f);
      if (f >= 1) { clearInterval(t); resolve(); }
    }, 240);
  });
}
