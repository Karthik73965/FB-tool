"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { isDemo, logout, session, type User } from "@/lib/api";
import { resetDemo } from "@/lib/demo";
import { Brand, Spinner } from "./ui";

/** top bar + login guard for every page except /login */
export default function Shell({ children, adminOnly }: { children: React.ReactNode; adminOnly?: boolean }) {
  const router = useRouter();
  const path = usePathname();
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    const s = session();
    if (!s) router.replace("/login");
    else if (adminOnly && !s.user.admin) router.replace("/");
    else setUser(s.user);
  }, [router, adminOnly]);

  return (
    <>
      <header className="topbar">
        <div className="wrap">
          <Link href="/" className="brand"><Brand /></Link>
          {user && (
            <nav className="nav">
              <Link href="/" className={path === "/" ? "on" : ""}>Studio</Link>
              {user.admin && <Link href="/admin" className={path === "/admin" ? "on" : ""}>Access</Link>}
            </nav>
          )}
          <span className="spacer" />
          {user && (
            <div className="who">
              <span className="hide-sm">{user.name}</span>
              <button className="btn sm" onClick={logout}>Log out</button>
            </div>
          )}
        </div>
      </header>
      {user && isDemo() && (
        <div className="demobar">
          <div className="wrap">
            <span className="dot" />
            <span><b>Demo mode.</b> Sample clips, nothing is uploaded or rendered.</span>
            <span className="spacer" />
            <button className="btn sm" onClick={() => { resetDemo(); location.reload(); }}>Reset demo</button>
          </div>
        </div>
      )}
      <main>
        <div className="wrap">{user ? children : <div className="center"><Spinner /></div>}</div>
      </main>
    </>
  );
}
