"use client";
import { useRef, useState } from "react";
import { api, bytes, putFile, type Job } from "@/lib/api";
import { Bar } from "./ui";

type Up = { name: string; size: number; frac: number; error?: string; done?: boolean };
const EXT = [".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi"];
const MAX_BYTES = 4 * 1024 ** 3;

/** read the size/length of a local video before uploading it (null if the browser can't play the format) */
function probe(file: File): Promise<{ w: number; h: number; d: number } | null> {
  return new Promise((resolve) => {
    const v = document.createElement("video");
    const url = URL.createObjectURL(file);
    const done = (r: { w: number; h: number; d: number } | null) => { URL.revokeObjectURL(url); resolve(r); };
    v.preload = "metadata";
    v.muted = true;
    v.onloadedmetadata = () => done(v.videoWidth ? { w: v.videoWidth, h: v.videoHeight, d: v.duration } : null);
    v.onerror = () => done(null);
    setTimeout(() => done(null), 8000);
    v.src = url;
  });
}

export default function Intake({ onChange }: { onChange: () => void }) {
  const [tab, setTab] = useState<"file" | "link">("file");
  const [over, setOver] = useState(false);
  const [ups, setUps] = useState<Up[]>([]);
  const [link, setLink] = useState("");
  const [linkErr, setLinkErr] = useState("");
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const patch = (i: number, p: Partial<Up>) => setUps((u) => u.map((x, k) => (k === i ? { ...x, ...p } : x)));

  async function send(files: File[]) {
    if (!files.length) return;
    const base = ups.filter((u) => !u.done && !u.error).length ? ups.length : 0;
    setUps((u) => [...(base ? u : []), ...files.map((f) => ({ name: f.name, size: f.size, frac: 0 }))]);
    for (let k = 0; k < files.length; k++) {
      const f = files[k], i = base + k;
      try {
        if (!EXT.some((e) => f.name.toLowerCase().endsWith(e))) throw new Error("Not a video file.");
        if (f.size > MAX_BYTES) throw new Error("Over the 4 GB limit.");
        const m = await probe(f);
        if (m && Math.abs(m.w / m.h - 16 / 9) > 0.05) throw new Error(`It's ${m.w}×${m.h}. Upload a landscape 16:9 video.`);
        if (m && m.d > 30 * 60) throw new Error(`It's ${Math.round(m.d / 60)} minutes long. The limit is 30.`);
        const r = await api<{ job: Job; upload_url: string }>("/jobs/upload", { body: { filename: f.name, size: f.size } });
        onChange();
        await putFile(r.upload_url, f, (frac) => patch(i, { frac }));
        await api(`/jobs/${r.job.id}/start`, { method: "POST" });
        patch(i, { frac: 1, done: true });
        onChange();
      } catch (e) {
        patch(i, { error: (e as Error).message });
      }
    }
  }

  async function submitLink(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setLinkErr("");
    try {
      await api("/jobs/link", { body: { url: link } });
      setLink("");
      onChange();
    } catch (x) {
      setLinkErr((x as Error).message);
    }
    setBusy(false);
  }

  return (
    <section className="panel stack" aria-label="New clip" style={{ marginBottom: 56 }}>
      <div className="panel-head" style={{ marginBottom: 0 }}>
        <div>
          <div className="eyebrow plain"><span className="num">01</span> / New clip</div>
          <h2 style={{ marginTop: 6 }}>Add a video</h2>
        </div>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "file"} className={tab === "file" ? "on" : ""} onClick={() => setTab("file")}>Upload</button>
          <button role="tab" aria-selected={tab === "link"} className={tab === "link" ? "on" : ""} onClick={() => setTab("link")}>Paste link</button>
        </div>
      </div>

      {tab === "file" ? (
        <>
          <div className={`drop ${over ? "over" : ""}`} role="button" tabIndex={0}
            onClick={() => input.current?.click()}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setOver(true); }}
            onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); send(Array.from(e.dataTransfer.files)); }}>
            <div className="drop-icon">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                <path d="M12 16V4M6 10l6-6 6 6M4 20h16" />
              </svg>
            </div>
            <strong>Drop a video here</strong>
            <span className="muted small">or tap to choose · landscape 16:9 · up to 30 min · 4 GB</span>
            <input ref={input} type="file" accept={"video/*," + EXT.join(",")} multiple hidden
              onChange={(e) => { send(Array.from(e.target.files || [])); e.target.value = ""; }} />
          </div>
          {ups.length > 0 && (
            <div className="stack" style={{ gap: 12 }}>
              {ups.map((u, i) => (
                <div key={i} className="stack" style={{ gap: 6 }}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className="small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "70%" }}>{u.name}</span>
                    <span className="mono small muted">
                      {u.error ? "" : u.done ? "SENT ✓" : `${Math.round(u.frac * 100)}% · ${bytes(u.size)}`}
                    </span>
                  </div>
                  {u.error ? <div className="alert small">{u.error}</div> : <Bar value={u.frac * 100} live={!u.done} info />}
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        <form className="stack" onSubmit={submitLink}>
          <input className="field" value={link} onChange={(e) => setLink(e.target.value)} type="url" inputMode="url"
            placeholder="https://…" aria-label="Video link" />
          {linkErr && <div className="alert small">{linkErr}</div>}
          <button className="btn solid" disabled={busy || !link.trim()}>{busy ? "Sending…" : "Fetch and render"}</button>
          <p className="muted small" style={{ margin: 0 }}>
            Only paste links to videos you have the rights to repost. Downloading from YouTube is against its terms of service.
          </p>
        </form>
      )}
    </section>
  );
}
