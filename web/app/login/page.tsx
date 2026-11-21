"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, saveSession, session, type User } from "@/lib/api";
import { Logo } from "@/components/ui";

export default function Login() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (session()) router.replace("/");
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api<{ token: string; user: User }>("/auth/login", { body: { code } });
      saveSession(r.token, r.user);
      router.replace("/");
    } catch (x) {
      setErr((x as Error).message);
      setBusy(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <div className="wrap">
          <span className="brand"><span className="brand-mark"><Logo /></span><span>FB<b>//</b>CLIP</span></span>
        </div>
      </header>
      <div className="wrap login">
        <form className="panel stack" onSubmit={submit}>
          <div className="reticle" aria-hidden>
            <svg viewBox="0 0 80 80" fill="none">
              <circle cx="40" cy="40" r="34" stroke="#00e6ff" strokeOpacity=".35" strokeDasharray="3 7" />
              <path d="M40 2v14M40 64v14M2 40h14M64 40h14" stroke="#00e6ff" strokeWidth="1.5" />
              <circle cx="40" cy="40" r="20" stroke="#00e6ff" strokeWidth="1.5" />
              <circle cx="40" cy="40" r="4" fill="#00e6ff" />
            </svg>
          </div>
          <div style={{ textAlign: "center" }}>
            <div className="eyebrow">Restricted access</div>
            <h1 style={{ fontSize: 26, marginTop: 8 }}>Enter your invite code</h1>
            <p className="muted small" style={{ margin: "8px 0 0" }}>You only need to do this once on each device.</p>
          </div>
          <input className="field code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="FB-XXXX-XXXX"
            autoFocus autoComplete="one-time-code" spellCheck={false} aria-label="Invite code" />
          {err && <div className="alert">{err}</div>}
          <button className="btn solid block" disabled={busy || !code.trim()}>{busy ? "Checking…" : "Unlock"}</button>
          <p className="muted small" style={{ textAlign: "center", margin: 0 }}>No code? Ask the person who runs this site.</p>
        </form>
      </div>
    </>
  );
}
