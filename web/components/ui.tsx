"use client";
import { ACTIVE, type Job } from "@/lib/api";

export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <rect x="9" y="2" width="14" height="28" rx="2" stroke="#00e6ff" strokeWidth="1.6" />
      <path d="M2 9v14M30 9v14" stroke="#00e6ff" strokeOpacity=".45" strokeWidth="1.6" />
      <path d="M13 21h6" stroke="#00e6ff" strokeWidth="2" strokeLinecap="round" />
      <circle cx="16" cy="13" r="3.2" fill="#00e6ff" />
    </svg>
  );
}

const LABEL: Record<Job["status"], string> = {
  uploading: "Uploading", queued: "Queued", fetching: "Downloading", running: "Rendering",
  done: "Ready", failed: "Failed", cancelled: "Cancelled",
};

export function StatusChip({ job }: { job: Job }) {
  const live = ACTIVE.includes(job.status);
  return (
    <span className={`chip ${job.status} ${live ? "live" : ""}`}>
      <i />
      {LABEL[job.status]}
    </span>
  );
}

export function Bar({ value, live, violet }: { value: number; live?: boolean; violet?: boolean }) {
  return (
    <div className={`bar ${live ? "live" : ""} ${violet ? "violet" : ""}`} role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${Math.max(2, Math.min(100, value))}%` }} />
    </div>
  );
}

export function Ring({ value, size = 120, label }: { value: number; size?: number; label?: string }) {
  const r = size / 2 - 8, c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="ring" aria-label={label}>
      <circle cx={size / 2} cy={size / 2} r={r} stroke="rgba(0,230,255,.12)" strokeWidth="6" fill="none" />
      <circle cx={size / 2} cy={size / 2} r={r + 5} stroke="rgba(0,230,255,.25)" strokeWidth="1" fill="none" strokeDasharray="2 6" />
      <circle cx={size / 2} cy={size / 2} r={r} stroke="#00e6ff" strokeWidth="6" fill="none" strokeLinecap="butt"
        strokeDasharray={`${(c * value) / 100} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ filter: "drop-shadow(0 0 6px #00e6ff)", transition: "stroke-dasharray .6s ease" }} />
      <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" fill="#e4f6ff"
        style={{ fontFamily: "var(--font-display)", fontSize: size / 4.6, fontWeight: 600 }}>
        {Math.round(value)}%
      </text>
    </svg>
  );
}

export function Spinner() {
  return <span className="spinner" aria-label="Loading" />;
}
