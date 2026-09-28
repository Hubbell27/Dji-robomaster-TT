import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ApiError, api, apiBlob } from "../lib/api";
import type { StaffMe } from "../lib/types";
import { AuditLog, Locations, StaffUsers } from "./Admin";
import { Dashboard, setOfficeTimezone } from "./Dashboard";
import { IntakeDetailPage } from "./IntakeDetail";
import {
  changePassword, clearSession, completeLogin, devLogin, getConfig, getToken, idleExpired, localLogin, localVerify,
  logout, startLogin, touch, type LocalStage,
} from "./auth";

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
      .then((m) => { setOfficeTimezone(m.office_timezone); setMe(m); setState("ok"); })
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
  if (state === "ok" && me?.must_change_password)
    return <ChangePassword onDone={() => window.location.reload()} />;
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
  const [mode, setMode] = useState<"cognito" | "local" | "dev" | null>(null);
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
        {mode === "local" && <LocalLogin />}
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

function LocalLogin() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [stage, setStage] = useState<LocalStage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!stage) {
    return (
      <form onSubmit={(e) => { e.preventDefault(); run(async () => setStage(await localLogin(email, password))); }}>
        <label className="field"><span className="field-label">Email</span>
          <input type="email" autoComplete="username" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label className="field"><span className="field-label">Password</span>
          <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary block" disabled={busy}>Continue</button>
      </form>
    );
  }

  return (
    <form onSubmit={(e) => {
      e.preventDefault();
      run(async () => { await localVerify(stage.challenge, code); window.location.assign("/staff"); });
    }}>
      {stage.stage === "enroll" ? (
        <>
          <h2>Set up your authenticator app</h2>
          <ol className="small">
            <li>Install an authenticator app on your phone (Google Authenticator, Microsoft Authenticator, Authy…).</li>
            <li>Add an account and scan this code.</li>
            <li>Enter the 6-digit code the app shows.</li>
          </ol>
          <div className="qr" dangerouslySetInnerHTML={{ __html: stage.qr_svg }} />
          <p className="muted small">Can't scan? Enter this key manually: <code>{stage.secret.match(/.{1,4}/g)?.join(" ")}</code></p>
        </>
      ) : (
        <p>Enter the 6-digit code from your authenticator app.</p>
      )}
      <label className="field"><span className="field-label">6-digit code</span>
        <input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9 ]{6,7}" maxLength={7} required autoFocus
          value={code} onChange={(e) => setCode(e.target.value)} className="code-input" /></label>
      {error && <p className="error" role="alert">{error}</p>}
      <button className="primary block" disabled={busy}>Verify</button>
      <button type="button" className="link" onClick={() => { setStage(null); setCode(""); setPassword(""); }}>Start over</button>
    </form>
  );
}

function ChangePassword({ onDone }: { onDone: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="staff-login">
      <form className="card narrow" onSubmit={async (e) => {
        e.preventDefault();
        if (next !== confirm) return setError("The new passwords don't match.");
        try { await changePassword(current, next); onDone(); } catch (err) { setError((err as Error).message); }
      }}>
        <h1>Choose your password</h1>
        <p className="muted">Replace the temporary password you were given. Use at least 12 characters with a mix of letters, numbers and symbols. A short sentence works well.</p>
        <label className="field"><span className="field-label">Temporary password</span>
          <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} /></label>
        <label className="field"><span className="field-label">New password</span>
          <input type="password" autoComplete="new-password" required minLength={12} value={next} onChange={(e) => setNext(e.target.value)} /></label>
        <label className="field"><span className="field-label">Confirm new password</span>
          <input type="password" autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} /></label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary block">Save password</button>
        <button type="button" className="link" onClick={() => logout()}>Sign out</button>
      </form>
    </div>
  );
}
