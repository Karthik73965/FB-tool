"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import Shell from "@/components/Shell";
import Review from "@/components/Review";
import { Bar, Device, MockClip, Ring, Spinner, StatusChip } from "@/components/ui";
import { ACTIVE, ago, api, bytes, clock, END_REASON, type Job } from "@/lib/api";

function JobView({ id }: { id: string }) {
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try { setJob(await api<Job>(`/jobs/${id}`)); setErr(""); }
    catch (e) { setErr((e as Error).message); }
  }, [id]);

  const live = !!job && (ACTIVE.includes(job.status) || job.status === "uploading");
  useEffect(() => {
    load();
    const t = setInterval(load, live ? 2000 : 30000);
    return () => clearInterval(t);
  }, [load, live]);

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const video = useMemo(() => job?.video_url, [job?.id, job?.result?.rendered_at]);

  async function act(path: string, method = "POST") {
    try {
      const r = await api<Job>(`/jobs/${id}/${path}`, { method });
      setJob(r);
    } catch (e) { setErr((e as Error).message); }
  }

  async function remove() {
    if (!confirm("Delete this clip and its files? This can't be undone.")) return;
    try { await api(`/jobs/${id}`, { method: "DELETE" }); router.replace("/"); }
    catch (e) { setErr((e as Error).message); }
  }

  if (!job) return err ? <div className="alert">{err} <Link href="/">Back to the studio</Link></div> : <div className="center"><Spinner /></div>;
  const r = job.result;
  const flagged = (r?.problems?.length ?? 0) > 0;

  return (
    <div className="stack" style={{ gap: 22 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <Link href="/" className="btn ghost sm">← Studio</Link>
        <span className="mono small muted">JOB {job.id.toUpperCase()}</span>
      </div>
      <div className="job-layout">
        <div className="device-col">
          <Device>
            {live ? (
              <div className="device-idle">
                <Ring value={job.pct} size={130} label="Progress" dark />
                <div className="label">{job.stage_text || "Starting"}</div>
                <div style={{ width: 200 }}><Bar value={job.pct} live /></div>
              </div>
            ) : video === "demo" ? (
              <MockClip />
            ) : video ? (
              <video key={r?.rendered_at} src={video} controls playsInline preload="metadata" />
            ) : (
              <div className="device-idle">
                <div className="label">{job.status === "failed" ? "Render failed" : "No clip"}</div>
              </div>
            )}
          </Device>
        </div>

        <div className="stack">
          <section className="panel stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <StatusChip job={job} />
              <span className="mono small muted">{job.source.kind === "link" ? "FROM LINK" : "UPLOAD"} · {ago(job.created)}{job.renders > 1 ? ` · VERSION ${job.renders}` : ""}</span>
            </div>
            <h1 style={{ fontSize: "clamp(26px, 3.6vw, 40px)", letterSpacing: "-0.045em", lineHeight: 1.05, wordBreak: "break-word" }}>{job.name}</h1>
            {job.source.url && <a className="small mono" href={job.source.url} target="_blank" rel="noreferrer" style={{ wordBreak: "break-all" }}>{job.source.url}</a>}

            {r && !live && (
              <div className="stats">
                <div className="stat"><span>Clip length</span><b>{clock(r.end, false)}</b></div>
                <div className="stat"><span>Source</span><b>{clock(r.duration, false)}</b></div>
                <div className="stat"><span>Ending</span><b className="txt">{END_REASON[r.end_reason]}</b></div>
                <div className="stat"><span>Glitch check</span><b className={flagged ? "bad" : "ok"}>{flagged ? `${r.problems.length} issue${r.problems.length > 1 ? "s" : ""}` : "Clean"}</b></div>
                <div className="stat"><span>File</span><b>{bytes(r.size)}</b></div>
              </div>
            )}

            {live && (
              <div className="alert info small">
                {job.status === "queued" ? "Waiting for a cloud GPU. Starting one takes up to a minute." : `${job.stage_text}.`}
                {r ? " The previous version stays below until this one is done." : " You can leave this page; it keeps going."}
              </div>
            )}
            {job.status === "failed" && <div className="alert">{job.error}</div>}
            {job.status === "cancelled" && <div className="alert warn">This job was cancelled.</div>}
            {flagged && !live && (
              <div className="alert warn small">Bad frames at {r!.problems.map((t) => clock(t)).join(", ")}.</div>
            )}

            <div className="row">
              {job.download_url && !live && (
                <a className="btn solid" href={job.download_url} onClick={(e) => { if (job.download_url === "#demo") { e.preventDefault(); alert("Demo mode: no real file to download."); } }}>Download MP4</a>
              )}
              {live && <button className="btn danger" onClick={() => act("cancel")}>Cancel</button>}
              {(job.status === "failed" || job.status === "cancelled") && <button className="btn" onClick={() => act("retry")}>Retry</button>}
              <span className="spacer" />
              {!live && <button className="btn ghost sm" onClick={remove}>Delete</button>}
            </div>
            {err && <div className="alert small">{err}</div>}
          </section>

          {r && !live && <Review job={job} onApplied={setJob} />}

          {job.status === "failed" && job.log_tail.length > 0 && (
            <section className="panel stack">
              <div className="label">Renderer log</div>
              <pre className="log">{job.log_tail.join("\n")}</pre>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

function Inner() {
  const id = useSearchParams().get("id") || "";
  return <JobView id={id} />;
}

export default function Page() {
  return (
    <Shell>
      <Suspense fallback={<div className="center"><Spinner /></div>}><Inner /></Suspense>
    </Shell>
  );
}
