"use client";
import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import Intake from "@/components/Intake";
import JobCard from "@/components/JobCard";
import { Device, MockClip, Spinner } from "@/components/ui";
import { ACTIVE, api, session, type Job } from "@/lib/api";

function Studio() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [err, setErr] = useState("");
  const admin = session()?.user.admin;

  const load = useCallback(async () => {
    try {
      setJobs(await api<Job[]>("/jobs"));
      setErr("");
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  const active = jobs?.some((j) => ACTIVE.includes(j.status) || j.status === "uploading");
  useEffect(() => {
    load();
    const t = setInterval(load, active ? 3000 : 15000);
    return () => clearInterval(t);
  }, [load, active]);

  const visible = (jobs || []).filter((j) => j.status !== "uploading");
  const running = visible.filter((j) => ACTIVE.includes(j.status)).length;

  return (
    <>
      <div className="hero">
        <div className="hero-copy">
          <div className="eyebrow">Studio <span className="muted">/</span> Vertical clips</div>
          <h1>Talk-show video in.<br /><span className="red">Reel out.</span></h1>
          <p className="lede">
            Speaker tracking, word-by-word captions, a waveform card when nobody&apos;s on screen, and the ad at the end
            cut off automatically. Rendered on a cloud GPU.
          </p>
          <div className="facts">
            <div><b>1080×1920</b><span>Output</span></div>
            <div><b>~4 min</b><span>Per 6-min video</span></div>
            <div><b>7 days</b><span>Kept online</span></div>
          </div>
        </div>
        <div className="hero-art">
          <Device tilt>
            <MockClip />
          </Device>
        </div>
      </div>

      <Intake onChange={load} />

      <div className="section-head">
        <div>
          <div className="eyebrow plain"><span className="num">02</span> / Queue</div>
          <h2 style={{ marginTop: 4 }}>{admin ? "All clips" : "Your clips"}</h2>
        </div>
        <span className="mono small muted">{running ? `${running} IN PROGRESS` : `${visible.length} TOTAL`}</span>
      </div>
      {err && <div className="alert" style={{ marginBottom: 14 }}>{err}</div>}
      {jobs === null ? (
        <div className="center" style={{ minHeight: 160 }}><Spinner /></div>
      ) : visible.length === 0 ? (
        <div className="empty">
          <div className="label">No clips yet</div>
          <p style={{ margin: "8px 0 0" }}>Upload a video or paste a link above to make your first clip.</p>
        </div>
      ) : (
        <div className="jobs">{visible.map((j) => <JobCard key={j.id} job={j} showOwner={admin} />)}</div>
      )}
    </>
  );
}

export default function Page() {
  return <Shell><Studio /></Shell>;
}
