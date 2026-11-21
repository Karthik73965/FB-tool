"use client";
import { useEffect, useMemo, useState } from "react";
import { api, clock, END_REASON, type Edits, type Job, type Review as R, type Sentence } from "@/lib/api";
import { Spinner } from "./ui";

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
const blank: Edits = { end: null, keep_outro: false, words: [], wave: [], face: [] };

/** ending + caption review; "Re-render" sends the edits and the API re-renders only the changed chunks */
export default function Review({ job, onApplied }: { job: Job; onApplied: (j: Job) => void }) {
  const [data, setData] = useState<R | null>(null);
  const [edits, setEdits] = useState<Edits>(blank);
  const [lines, setLines] = useState<Sentence[]>([]);
  const [tab, setTab] = useState<"end" | "captions">("end");
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const rendered = job.result?.rendered_at;

  useEffect(() => {
    let off = false;
    api<R>(`/jobs/${job.id}/review`).then((r) => {
      if (off) return;
      const e = { ...blank, ...r.edits };
      setData({ ...r, edits: e });
      setEdits(e);
      setLines(r.sentences);
    }).catch((e) => setErr((e as Error).message));
    return () => { off = true; };
  }, [job.id, rendered]);

  const res = data?.result;
  const saved = data?.edits;
  const dirty = !!saved && !same(edits, saved);
  // where the clip will end with the current edits (null = auto-detect on the next render)
  const endT = edits.end != null ? edits.end : edits.keep_outro ? res?.duration ?? null
    : saved && saved.end == null && !saved.keep_outro ? res?.end ?? null : null;
  const mode = edits.end != null ? "custom" : edits.keep_outro ? "keep" : "auto";

  const tail = useMemo(() => {
    if (!res) return [];
    const from = res.duration - 150;
    return lines.map((s, i) => ({ s, i })).filter(({ s }) => s.e >= from);
  }, [lines, res]);

  async function preview(e: Edits) {
    setEdits(e);
    try {
      setLines((await api<{ sentences: Sentence[] }>(`/jobs/${job.id}/preview`, { body: { edits: e } })).sentences);
    } catch (x) { setErr((x as Error).message); }
  }

  function endAfter(i: number) {
    const s = lines[i], nx = lines[i + 1];
    const t = nx ? Math.min(s.e + 0.3, nx.s - 0.05) : s.e + 0.3;
    setEdits({ ...edits, end: Math.round(Math.max(t, s.e) * 100) / 100, keep_outro: false });
  }

  async function saveLine(i: number) {
    const text = draft.trim();
    setEditing(null);
    if (text === lines[i].text) return;
    try {
      const r = await api<{ edits: Edits; sentences: Sentence[] }>(`/jobs/${job.id}/fix`, { body: { edits, si: i, text } });
      setEdits({ ...edits, ...r.edits });
      setLines(r.sentences);
    } catch (x) { setErr((x as Error).message); }
  }

  function revertLine(i: number) {
    const s = lines[i];
    preview({ ...edits, words: edits.words.filter((w) => w.at < s.s - 0.6 || w.at > s.e + 0.01) });
  }

  async function apply() {
    setBusy(true);
    setErr("");
    try { onApplied(await api<Job>(`/jobs/${job.id}/apply`, { body: { edits } })); }
    catch (x) { setErr((x as Error).message); setBusy(false); }
  }

  if (!data) return <section className="panel">{err ? <div className="alert">{err}</div> : <div className="center" style={{ minHeight: 120 }}><Spinner /></div>}</section>;

  const caps = lines.map((s, i) => ({ s, i }))
    .filter(({ s }) => (endT == null || s.s < endT) && (!query || s.text.toLowerCase().includes(query.toLowerCase())));
  const lastKept = endT == null ? -1 : tail.reduce((k, { s, i }) => (s.e <= endT + 0.31 ? i : k), -1);

  return (
    <section className="panel stack" aria-label="Review">
      <div className="panel-head" style={{ marginBottom: 0 }}>
        <div>
          <div className="eyebrow">Review</div>
          <h2 style={{ marginTop: 4 }}>Fix the ending and captions</h2>
        </div>
        <div className="tabs" role="tablist">
          <button role="tab" aria-selected={tab === "end"} className={tab === "end" ? "on" : ""} onClick={() => setTab("end")}>Ending</button>
          <button role="tab" aria-selected={tab === "captions"} className={tab === "captions" ? "on" : ""} onClick={() => setTab("captions")}>
            Captions{edits.words.length ? ` · ${edits.words.length}` : ""}
          </button>
        </div>
      </div>
      {err && <div className="alert">{err}</div>}

      {tab === "end" ? (
        <div className="stack">
          <div className="alert info small">
            Last render ends at <b className="mono">{clock(res!.end)}</b> of {clock(res!.duration)}: {END_REASON[res!.end_reason]}
            {res!.ad_start != null && <> (ad starts at <span className="mono">{clock(res!.ad_start)}</span>)</>}.
            Ads, bumpers and logo screens should be cut off.
          </div>
          <div className="row">
            <div className="tabs">
              <button className={mode === "auto" ? "on" : ""} onClick={() => setEdits({ ...edits, end: null, keep_outro: false })}>Auto-detect</button>
              <button className={mode === "keep" ? "on" : ""} onClick={() => setEdits({ ...edits, end: null, keep_outro: true })}>Keep everything</button>
              <button className={mode === "custom" ? "on" : ""} onClick={() => setEdits({ ...edits, end: Math.round((endT ?? res!.end) * 100) / 100, keep_outro: false })}>Custom</button>
            </div>
            {mode === "custom" && (
              <label className="row small muted">
                End at
                <input className="field mono" style={{ width: 120, minHeight: 36, padding: "6px 10px" }} type="number" step="0.05" min={1}
                  max={res!.duration} value={edits.end ?? ""} onChange={(e) => setEdits({ ...edits, end: e.target.value === "" ? null : Number(e.target.value) })} />
                s <span className="mono">({clock(edits.end)})</span>
              </label>
            )}
          </div>
          <p className="muted small" style={{ margin: 0 }}>Last {Math.min(150, Math.round(res!.duration))} seconds of the source. Press <b>End here</b> on the last line of the real conversation.</p>
          <div className="lines">
            {tail.map(({ s, i }) => {
              const cut = endT != null && s.s >= endT;
              const ad = res!.ad_start != null && Math.abs(s.s - res!.ad_start) < 0.05;
              return (
                <div key={i}>
                  <div className={`line ${cut ? "cut" : ""}`}>
                    <time>{clock(s.s)}</time>
                    <span className="text">
                      {ad && <span className="chip failed" style={{ marginRight: 8 }}>Ad read</span>}
                      {s.text}
                    </span>
                    <span className="acts"><button className="btn sm" onClick={() => endAfter(i)}>End here</button></span>
                  </div>
                  {i === lastKept && <div className="endmark">Clip ends · {clock(endT)}</div>}
                </div>
              );
            })}
          </div>
        </div>
      ) : (
        <div className="stack">
          <div className="row">
            <input className="field" style={{ flex: 1, minWidth: 200 }} placeholder="Search captions…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <p className="muted small" style={{ margin: 0 }}>
            Click a line to fix misheard words (names especially). Remove a word to delete it. Swear words are masked on screen automatically.
          </p>
          <div className="lines">
            {caps.map(({ s, i }) => (
              <div key={i} className={`line ${s.fixed ? "fixed" : ""}`}>
                <time>{clock(s.s)}</time>
                {editing === i ? (
                  <input className="field" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
                    onBlur={() => saveLine(i)}
                    onKeyDown={(e) => { if (e.key === "Enter") e.currentTarget.blur(); if (e.key === "Escape") setEditing(null); }} />
                ) : (
                  <span className="text" role="button" tabIndex={0} onClick={() => { setEditing(i); setDraft(s.text); }}
                    onKeyDown={(e) => e.key === "Enter" && (setEditing(i), setDraft(s.text))}>{s.text}</span>
                )}
                <span className="acts">{s.fixed && editing !== i && <button className="btn ghost sm" onClick={() => revertLine(i)}>Revert</button>}</span>
              </div>
            ))}
            {!caps.length && <div className="muted small">No lines match.</div>}
          </div>
        </div>
      )}

      {dirty && (
        <div className="savebar">
          <span className="pulse" style={{ background: "var(--amber)", boxShadow: "0 0 10px var(--amber)" }} />
          <span className="small" style={{ flex: 1, minWidth: 160 }}>Unsaved changes. Only the changed parts re-render (about 10–30 s plus GPU start-up).</span>
          <button className="btn ghost sm" onClick={() => preview(saved!)} disabled={busy}>Discard</button>
          <button className="btn solid sm" onClick={apply} disabled={busy}>{busy ? "Starting…" : "Re-render clip"}</button>
        </div>
      )}
    </section>
  );
}
