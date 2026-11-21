"use client";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, saveSession, session, type User } from "@/lib/api";
import { Brand } from "@/components/ui";

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
        <div className="wrap"><span className="brand"><Brand /></span></div>
      </header>
      <div className="wrap login">
        <div className="hero-copy">
          <div className="eyebrow">Invite only <span className="muted">/</span> Clip maker</div>
          <h1>Long video in.<br /><span className="red">Vertical clip out.</span></h1>
          <p className="lede">Speaker tracking, word-by-word captions and the outro cut off, rendered on a cloud GPU.</p>
        </div>
        <form className="panel stack" onSubmit={submit}>
          <div>
            <div className="label">Sign in</div>
            <h2 style={{ marginTop: 6 }}>Enter your invite code</h2>
            <p className="muted small" style={{ margin: "6px 0 0" }}>You only need to do this once on each device.</p>
          </div>
          <input className="field code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="FB-XXXX-XXXX"
            autoFocus autoComplete="one-time-code" spellCheck={false} aria-label="Invite code" />
          {err && <div className="alert">{err}</div>}
          <button className="btn solid block" disabled={busy || !code.trim()}>{busy ? "Checking…" : "Continue →"}</button>
          <p className="muted small" style={{ margin: 0 }}>No code? Ask the person who runs this site.</p>
        </form>
      </div>
    </>
  );
}
