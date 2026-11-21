"use client";
import Link from "next/link";
import { useMemo } from "react";
import { ACTIVE, ago, clock, type Job } from "@/lib/api";
import { Bar, Ring, StatusChip } from "./ui";

export default function JobCard({ job, showOwner }: { job: Job; showOwner?: boolean }) {
  const live = ACTIVE.includes(job.status);
  // presigned URLs change on every poll; keep the first one per render so the preview doesn't reload
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const preview = useMemo(() => job.video_url, [job.id, job.result?.rendered_at]);
  const r = job.result;

  return (
    <Link href={`/job?id=${job.id}`} className="panel card">
      <div className="thumb">
        {preview && !live ? (
          <video src={`${preview}#t=1`} preload="metadata" muted playsInline />
        ) : live ? (
          <Ring value={job.pct} size={64} label="Progress" dark />
        ) : (
          <span className="mono small muted">{job.status === "failed" ? "ERR" : "—"}</span>
        )}
      </div>
      <div className="card-body">
        <div className="row" style={{ justifyContent: "space-between", flexWrap: "nowrap" }}>
          <StatusChip job={job} />
          <span className="mono small muted">{ago(job.created)}</span>
        </div>
        <div className="card-title" title={job.name}>{job.name}</div>
        {live ? (
          <>
            <Bar value={job.pct} live />
            <div className="meta"><span>{job.stage_text || "Starting"}</span><span>{job.pct}%</span></div>
          </>
        ) : job.status === "failed" ? (
          <div className="small" style={{ color: "var(--bad)", overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
            {job.error}
          </div>
        ) : r ? (
          <div className="meta">
            <span>{clock(r.end, false)} long</span>
            {r.end_reason === "ad" && <span>ad cut</span>}
            {job.renders > 1 && <span>v{job.renders}</span>}
          </div>
        ) : null}
        {showOwner && <div className="meta"><span>by {job.owner_name}</span></div>}
      </div>
    </Link>
  );
}
