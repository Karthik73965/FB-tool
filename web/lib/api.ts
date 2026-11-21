// Talks to the FB Clip Maker API (cloud/fbclip/api.py). The session token lives in localStorage.
import { DEMO_KEY, DEMO_TOKEN, DEMO_USER, demoApi, demoPut } from "./demo";

export const API = (process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const TOKEN = "fbclip.token";
const USER = "fbclip.user";

export type User = { id: string; name: string; admin: boolean };
export type Edits = { end: number | null; keep_outro?: boolean; words: { at: number; to: string }[]; wave: number[][]; face: number[][] };
export type Result = {
  duration: number; end: number; end_reason: "full" | "manual" | "ad" | "logo"; ad_start: number | null;
  encoder: string; device: string; problems: number[]; size: number; rendered_at: number; edits?: Edits;
};
export type Job = {
  id: string; name: string; status: "uploading" | "queued" | "fetching" | "running" | "done" | "failed" | "cancelled";
  pct: number; stage_text: string; error: string | null; created: number; updated: number; owner_name: string;
  renders: number; edits: Edits; result: Result | null; log_tail: string[];
  source: { kind: "upload" | "link"; url?: string; filename?: string; size?: number };
  video_url: string | null; download_url: string | null;
};
export type Sentence = { s: number; e: number; text: string; fixed: boolean };
export type Review = { result: Result; edits: Edits; sentences: Sentence[] };
export type Invite = { id: string; name: string; code: string; created: number; revoked: boolean; jobs: number };
export type Me = User & { limits: { active: number; bytes: number; seconds: number; keep_days: number; ext: string[] } };

export const isDemo = () => session()?.token === DEMO_TOKEN;

export const ACTIVE = ["queued", "fetching", "running"];

function read(k: string) {
  try { return localStorage.getItem(k); } catch { return null; }
}

export function session(): { token: string; user: User } | null {
  const t = read(TOKEN), u = read(USER);
  if (!t || !u) return null;
  try { return { token: t, user: JSON.parse(u) }; } catch { return null; }
}

export function saveSession(token: string, user: User) {
  try { localStorage.setItem(TOKEN, token); localStorage.setItem(USER, JSON.stringify(user)); } catch { /* private mode */ }
}

export function logout() {
  try { localStorage.removeItem(TOKEN); localStorage.removeItem(USER); } catch { /* ignore */ }
  window.location.href = "/login";
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T = unknown>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const s = session();
  const method = opts.method || (opts.body !== undefined ? "POST" : "GET");
  const code = (opts.body as { code?: string } | undefined)?.code;
  if (path === "/auth/login" && code?.trim().toUpperCase() === DEMO_KEY) return { token: DEMO_TOKEN, user: DEMO_USER } as T;
  if (s?.token === DEMO_TOKEN) {
    try { return (await demoApi(path, method, opts.body as Record<string, unknown>)) as T; }
    catch (e) { throw new ApiError((e as { status?: number }).status || 400, (e as Error).message); }
  }
  let r: Response;
  try {
    r = await fetch(API + path, {
      method,
      headers: { "Content-Type": "application/json", ...(s ? { Authorization: `Bearer ${s.token}` } : {}) },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Can't reach the server. Check your connection.");
  }
  if (r.status === 401 && path !== "/auth/login") {
    logout();
    throw new ApiError(401, "Please log in again.");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, typeof data.detail === "string" ? data.detail : `Error ${r.status}`);
  return data as T;
}

/** PUT a file straight to storage (presigned URL), reporting progress 0..1 */
export function putFile(url: string, file: File, onProgress: (f: number) => void, signal?: AbortSignal) {
  if (url.startsWith("demo:")) return demoPut(onProgress);
  return new Promise<void>((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("PUT", url);
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => (x.status >= 200 && x.status < 300 ? resolve() : reject(new Error(`Upload failed (${x.status})`)));
    x.onerror = () => reject(new Error("Upload failed. Check your connection and try again."));
    x.onabort = () => reject(new Error("Upload cancelled."));
    signal?.addEventListener("abort", () => x.abort());
    x.send(file);
  });
}

// ---------------------------------------------------------------- formatting
export function clock(t: number | null | undefined, frac = true) {
  if (t == null || isNaN(t)) return "—";
  const m = Math.floor(t / 60), s = t - m * 60;
  return `${m}:${frac ? s.toFixed(2).padStart(5, "0") : String(Math.floor(s)).padStart(2, "0")}`;
}

export function bytes(n?: number) {
  if (!n) return "—";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i > 1 ? 1 : 0)} ${u[i]}`;
}

export function ago(ts: number) {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} d ago`;
}

export const END_REASON: Record<string, string> = {
  ad: "Ad read / bumper cut automatically",
  logo: "Logo screen trimmed",
  manual: "Ending set by hand",
  full: "Full length",
};
