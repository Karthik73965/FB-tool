"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { logout, session, type User } from "@/lib/api";
import { Logo, Spinner } from "./ui";

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
          <Link href="/" className="brand">
            <span className="brand-mark"><Logo /></span>
            <span>FB<b>//</b>CLIP</span>
          </Link>
          {user && (
            <nav className="nav">
              <Link href="/" className={path === "/" ? "on" : ""}>Studio</Link>
              {user.admin && <Link href="/admin" className={path === "/admin" ? "on" : ""}>Access</Link>}
            </nav>
          )}
          <span className="spacer" />
          {user && (
            <div className="who">
              <span className="pulse" />
              <span className="hide-sm">{user.name.toUpperCase()}</span>
              <button className="btn ghost sm" onClick={logout}>Log out</button>
            </div>
          )}
        </div>
      </header>
      <main>
        <div className="wrap">{user ? children : <div className="center"><Spinner /></div>}</div>
      </main>
    </>
  );
}
