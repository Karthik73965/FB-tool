"use client";
import { useCallback, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { Spinner } from "@/components/ui";
import { ago, api, type Invite } from "@/lib/api";

function Access() {
  const [list, setList] = useState<Invite[] | null>(null);
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<Invite | null>(null);
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [copied, setCopied] = useState("");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try { setList(await api<Invite[]>("/admin/invites")); }
    catch (e) { setErr((e as Error).message); }
  }, []);
  useEffect(() => { load(); }, [load]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setErr("");
    try {
      setFresh(await api<Invite>("/admin/invites", { body: { name } }));
      setName("");
      load();
    } catch (x) { setErr((x as Error).message); }
  }

  async function revoke(u: Invite) {
    if (!confirm(`Revoke ${u.name}'s code? They'll be logged out everywhere.`)) return;
    try { await api(`/admin/invites/${u.id}`, { method: "DELETE" }); load(); }
    catch (x) { setErr((x as Error).message); }
  }

  async function copy(code: string) {
    try { await navigator.clipboard.writeText(code); setCopied(code); setTimeout(() => setCopied(""), 1500); }
    catch { /* clipboard blocked: the code is on screen */ }
  }

  return (
    <div className="stack" style={{ gap: 22 }}>
      <div>
        <div className="eyebrow">Access control</div>
        <h1 className="page-title" style={{ marginTop: 10 }}>Invite codes</h1>
        <p className="lede" style={{ marginTop: 14 }}>One code per person. They enter it once on each device. Revoking a code logs that person out everywhere.</p>
      </div>

      <section className="panel stack">
        <form className="row" onSubmit={create}>
          <input className="field" style={{ flex: 1, minWidth: 200 }} placeholder="Person's name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
          <button className="btn solid" disabled={!name.trim()}>Create code</button>
        </form>
        {fresh && (
          <div className="alert ok stack" style={{ gap: 8 }}>
            <span className="small">Code for <b>{fresh.name}</b>. Send it to them privately:</span>
            <div className="row">
              <span className="codebox">{fresh.code}</span>
              <button className="btn sm" onClick={() => copy(fresh.code)}>{copied === fresh.code ? "Copied ✓" : "Copy"}</button>
            </div>
          </div>
        )}
        {err && <div className="alert small">{err}</div>}
      </section>

      <section className="panel">
        {list === null ? <div className="center" style={{ minHeight: 120 }}><Spinner /></div> : list.length === 0 ? (
          <div className="muted">No invite codes yet.</div>
        ) : (
          <div className="scroll-x">
            <table className="table">
              <thead><tr><th>Name</th><th>Code</th><th className="hide-sm">Created</th><th>Clips</th><th /></tr></thead>
              <tbody>
                {list.map((u) => (
                  <tr key={u.id} className={u.revoked ? "revoked" : ""}>
                    <td>{u.name}</td>
                    <td className="mono">
                      {u.revoked ? "revoked" : shown[u.id] ? u.code : "FB-••••-••••"}
                      {!u.revoked && (
                        <>
                          {" "}
                          <button className="btn ghost sm" onClick={() => setShown({ ...shown, [u.id]: !shown[u.id] })}>{shown[u.id] ? "Hide" : "Show"}</button>
                          <button className="btn ghost sm" onClick={() => copy(u.code)}>{copied === u.code ? "✓" : "Copy"}</button>
                        </>
                      )}
                    </td>
                    <td className="hide-sm muted small">{ago(u.created)}</td>
                    <td className="mono">{u.jobs}</td>
                    <td style={{ textAlign: "right" }}>{!u.revoked && <button className="btn danger sm" onClick={() => revoke(u)}>Revoke</button>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

export default function Page() {
  return <Shell adminOnly><Access /></Shell>;
}
