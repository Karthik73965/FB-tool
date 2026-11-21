"use client";
import { ACTIVE, type Job } from "@/lib/api";

/** a little vertical phone made of pixels */
export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-hidden shapeRendering="crispEdges">
      <path fill="#f0432d" d="M4 0h8v1h1v14h-1v1H4v-1H3V1h1zM5 2v9h6V2zm2 11v1h2v-1z" />
      <path fill="#20231e" d="M6 4h4v2H6zM7 7h2v2H7z" />
    </svg>
  );
}

export function Brand() {
  return (
    <>
      <span className="brand-mark"><Logo /></span>
      <span>fb-clip</span>
    </>
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

export function Bar({ value, live, info }: { value: number; live?: boolean; info?: boolean }) {
  return (
    <div className={`bar ${live ? "live" : ""} ${info ? "info" : ""}`} role="progressbar" aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ width: `${Math.max(2, Math.min(100, value))}%` }} />
    </div>
  );
}

export function Ring({ value, size = 120, label, dark }: { value: number; size?: number; label?: string; dark?: boolean }) {
  const r = size / 2 - 6, c = 2 * Math.PI * r;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-label={label}>
      <circle cx={size / 2} cy={size / 2} r={r} stroke={dark ? "rgba(255,255,255,.15)" : "rgba(32,35,30,.1)"} strokeWidth="4" fill="none" />
      <circle cx={size / 2} cy={size / 2} r={r} stroke="#f0432d" strokeWidth="4" fill="none"
        strokeDasharray={`${(c * value) / 100} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dasharray .6s ease" }} />
      <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" fill={dark ? "#fff" : "#20231e"}
        style={{ fontFamily: "var(--font-sans)", fontSize: size / 4.4, fontWeight: 700, letterSpacing: "-0.04em" }}>
        {Math.round(value)}%
      </text>
    </svg>
  );
}

/** the phone frame used on the home page and the job page */
export function Device({ children, tilt }: { children: React.ReactNode; tilt?: boolean }) {
  return (
    <div className={`device ${tilt ? "tilt" : ""}`}>
      <div className="screen"><span className="island" />{children}</div>
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-label="Loading" />;
}
