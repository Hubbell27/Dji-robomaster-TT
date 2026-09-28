import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ApiError, api, apiBlob } from "../lib/api";
import type { StaffMe } from "../lib/types";
import { AuditLog, Locations, StaffUsers } from "./Admin";
import { Dashboard } from "./Dashboard";
import { IntakeDetailPage } from "./IntakeDetail";
import { clearSession, completeLogin, devLogin, getConfig, getToken, idleExpired, logout, startLogin, touch } from "./auth";

interface StaffCtx {
  me: StaffMe;
  call: <T>(path: string, opts?: { method?: string; body?: unknown }) => Promise<T>;
  blob: (path: string) => Promise<Blob>;
}

const Ctx = createContext<StaffCtx | null>(null);
export const useStaff = () => useContext(Ctx)!;

export function StaffApp() {
  return (
    <Routes>
      <Route path="callback" element={<Callback />} />
      <Route path="*" element={<Authed />} />
    </Routes>
  );
}

function Callback() {
  const navigate = useNavigate();
  const loc = useLocation();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    completeLogin(loc.search).then(() => navigate("/staff", { replace: true })).catch((e) => setError(e.message));
  }, [loc.search, navigate]);
  return <div className="staff-login"><div className="card narrow">{error ?? "Signing in…"}</div></div>;
}

function Authed() {
  const [me, setMe] = useState<StaffMe | null>(null);
  const [state, setState] = useState<"loading" | "signed-out" | "forbidden" | "ok">("loading");
  const [message, setMessage] = useState<string | null>(null);

  const call = useCallback(async <T,>(path: string, opts: { method?: string; body?: unknown } = {}) => {
    const token = await getToken();
    if (!token) {
      setState("signed-out");
      throw new ApiError(401, "Signed out");
    }
    touch();
    try {
      return await api<T>(path, { ...opts, token });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        clearSession();
        setState("signed-out");
      }
      throw e;
    }
  }, []);

  const blob = useCallback(async (path: string) => {
    const token = await getToken();
    touch();
    return apiBlob(path, token);
  }, []);

  useEffect(() => {
    call<StaffMe>("/api/staff/me")
      .then((m) => { setMe(m); setState("ok"); })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 403) { setMessage(e.message); setState("forbidden"); }
        else setState("signed-out");
      });
  }, [call]);

  // Idle timeout: record activity, sign out after 15 minutes without any.
  useEffect(() => {
    if (state !== "ok") return;
    let last = 0;
    const onActivity = () => {
      if (Date.now() - last > 10_000) { last = Date.now(); touch(); }
    };
    const events = ["mousemove", "keydown", "pointerdown", "scroll"];
    events.forEach((e) => window.addEventListener(e, onActivity, { passive: true }));
    const timer = window.setInterval(() => {
      if (idleExpired()) {
        clearSession();
        setMessage("You were signed out after 15 minutes of inactivity.");
        setState("signed-out");
      }
    }, 20_000);
    return () => {
      events.forEach((e) => window.removeEventListener(e, onActivity));
      window.clearInterval(timer);
    };
  }, [state]);

  if (state === "loading") return <div className="staff-login"><div className="card narrow">Loading…</div></div>;
  if (state === "signed-out") return <Login message={message} />;
  if (state === "forbidden" || !me)
    return <div className="staff-login"><div className="card narrow"><p>{message}</p><button className="secondary" onClick={() => logout()}>Sign out</button></div></div>;

  return (
    <Ctx.Provider value={{ me, call, blob }}>
      <div className="staff">
        <nav className="staff-nav">
          <div className="brand">Patient Intake</div>
          <NavLink to="/staff" end>Dashboard</NavLink>
          {me.role === "admin" && (
            <>
              <NavLink to="/staff/admin/staff">Staff</NavLink>
              <NavLink to="/staff/admin/locations">Locations</NavLink>
              <NavLink to="/staff/admin/audit">Audit log</NavLink>
            </>
          )}
          <div className="spacer" />
          <span className="muted small">{me.full_name} · {me.role === "admin" ? "Admin" : "Front desk"}</span>
          <button className="link" onClick={() => logout()}>Sign out</button>
        </nav>
        <main className="staff-main">
          <Routes>
            <Route index element={<Dashboard />} />
            <Route path="intakes/:id" element={<IntakeDetailPage />} />
            {me.role === "admin" && (
              <>
                <Route path="admin/staff" element={<StaffUsers />} />
                <Route path="admin/locations" element={<Locations />} />
                <Route path="admin/audit" element={<AuditLog />} />
              </>
            )}
            <Route path="*" element={<p>Not found.</p>} />
          </Routes>
        </main>
      </div>
    </Ctx.Provider>
  );
}

function Login({ message }: { message: string | null }) {
  const [mode, setMode] = useState<"cognito" | "dev" | null>(null);
  const [devUsers, setDevUsers] = useState<{ email: string; full_name: string; role: string }[]>([]);

  useEffect(() => {
    getConfig().then((c) => {
      setMode(c.auth_mode);
      if (c.auth_mode === "dev") api<typeof devUsers>("/api/dev/users").then(setDevUsers);
    });
  }, []);

  return (
    <div className="staff-login">
      <div className="card narrow">
        <h1>Staff sign-in</h1>
        {message && <p className="notice">{message}</p>}
        <p className="muted">Authorized practice staff only. All access to patient information is logged.</p>
        {mode === "cognito" && <button className="primary" onClick={() => startLogin()}>Sign in</button>}
        {mode === "dev" && (
          <>
            <p className="notice">Development mode: choose a seeded user (never enabled in production).</p>
            {devUsers.map((u) => (
              <button key={u.email} className="secondary block"
                onClick={() => devLogin(u.email).then(() => window.location.assign("/staff"))}>
                {u.full_name} ({u.role})
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
