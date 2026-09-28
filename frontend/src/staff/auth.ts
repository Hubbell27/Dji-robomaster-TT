/**
 * Staff sign-in via the Cognito managed login (authorization code + PKCE).
 * The user pool enforces MFA, so no MFA UI lives here. Tokens are kept in
 * sessionStorage (scoped to the tab, cleared when it closes) and the session
 * ends after 15 minutes without activity.
 */
import { api } from "../lib/api";

interface PublicConfig {
  auth_mode: "cognito" | "local" | "dev";
  deployment: "aws" | "office";
  cognito_domain: string;
  cognito_client_id: string;
}

interface Tokens {
  id_token: string;
  refresh_token?: string;
  expires_at: number; // epoch ms
}

const TOKENS = "staff.tokens";
const PKCE = "staff.pkce";
const LAST_ACTIVE = "staff.lastActive";
export const IDLE_LIMIT_MS = 15 * 60 * 1000;

let configPromise: Promise<PublicConfig> | null = null;
export const getConfig = () => (configPromise ??= api<PublicConfig>("/api/config"));

const redirectUri = () => `${window.location.origin}/staff/callback`;

function b64url(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  return btoa(String.fromCharCode(...arr)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function randomString(len = 48): string {
  return b64url(crypto.getRandomValues(new Uint8Array(len)));
}

function decodeExp(jwt: string): number {
  const payload = JSON.parse(atob(jwt.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
  return payload.exp * 1000;
}

function save(tokens: Tokens) {
  sessionStorage.setItem(TOKENS, JSON.stringify(tokens));
  touch();
}

export function touch() {
  sessionStorage.setItem(LAST_ACTIVE, String(Date.now()));
}

export function idleExpired(): boolean {
  const last = Number(sessionStorage.getItem(LAST_ACTIVE) || 0);
  return Date.now() - last > IDLE_LIMIT_MS;
}

export async function startLogin(): Promise<void> {
  const cfg = await getConfig();
  const verifier = randomString();
  const state = randomString(16);
  sessionStorage.setItem(PKCE, JSON.stringify({ verifier, state }));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const params = new URLSearchParams({
    response_type: "code",
    client_id: cfg.cognito_client_id,
    redirect_uri: redirectUri(),
    scope: "openid email profile",
    state,
    code_challenge: challenge,
    code_challenge_method: "S256",
  });
  window.location.assign(`${cfg.cognito_domain}/oauth2/authorize?${params}`);
}

export async function completeLogin(search: string): Promise<void> {
  const cfg = await getConfig();
  const q = new URLSearchParams(search);
  const stored = JSON.parse(sessionStorage.getItem(PKCE) || "{}");
  sessionStorage.removeItem(PKCE);
  if (!q.get("code") || q.get("state") !== stored.state) throw new Error("Sign-in failed. Please try again.");
  const res = await fetch(`${cfg.cognito_domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: cfg.cognito_client_id,
      code: q.get("code")!,
      redirect_uri: redirectUri(),
      code_verifier: stored.verifier,
    }),
  });
  if (!res.ok) throw new Error("Sign-in failed. Please try again.");
  const data = await res.json();
  save({ id_token: data.id_token, refresh_token: data.refresh_token, expires_at: decodeExp(data.id_token) });
}

// ------------------------------------------------------------ office (local) mode

export type LocalStage =
  | { stage: "mfa"; challenge: string }
  | { stage: "enroll"; challenge: string; qr_svg: string; secret: string; otpauth_uri: string };

export async function localLogin(email: string, password: string): Promise<LocalStage> {
  return api<LocalStage>("/api/auth/login", { method: "POST", body: { email, password } });
}

interface LocalSession { token: string; expires_at: number; must_change_password: boolean }

function saveLocal(s: LocalSession) {
  save({ id_token: s.token, expires_at: s.expires_at * 1000 });
}

export async function localVerify(challenge: string, code: string): Promise<LocalSession> {
  const s = await api<LocalSession>("/api/auth/verify-mfa", { method: "POST", body: { challenge, code } });
  saveLocal(s);
  return s;
}

export async function changePassword(current_password: string, new_password: string): Promise<void> {
  const token = await getToken();
  const s = await api<LocalSession>("/api/auth/change-password", {
    method: "POST", token, body: { current_password, new_password },
  });
  saveLocal(s);
}

export async function devLogin(email: string): Promise<void> {
  const data = await api<{ id_token: string }>("/api/dev/login", { method: "POST", body: { email } });
  save({ id_token: data.id_token, expires_at: decodeExp(data.id_token) });
}

async function refresh(tokens: Tokens): Promise<Tokens | null> {
  const cfg = await getConfig();
  if (cfg.auth_mode === "local") {
    try {
      const s = await api<LocalSession>("/api/auth/refresh", { method: "POST", token: tokens.id_token });
      const next = { id_token: s.token, expires_at: s.expires_at * 1000 };
      sessionStorage.setItem(TOKENS, JSON.stringify(next));
      return next;
    } catch {
      return null;
    }
  }
  if (!tokens.refresh_token || cfg.auth_mode !== "cognito") return null;
  const res = await fetch(`${cfg.cognito_domain}/oauth2/token`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ grant_type: "refresh_token", client_id: cfg.cognito_client_id, refresh_token: tokens.refresh_token }),
  });
  if (!res.ok) return null;
  const data = await res.json();
  const next = { ...tokens, id_token: data.id_token, expires_at: decodeExp(data.id_token) };
  sessionStorage.setItem(TOKENS, JSON.stringify(next));
  return next;
}

/** Returns a valid ID token, refreshing if close to expiry; null if signed out. */
export async function getToken(): Promise<string | null> {
  const raw = sessionStorage.getItem(TOKENS);
  if (!raw) return null;
  if (idleExpired()) {
    clearSession();
    return null;
  }
  let tokens = JSON.parse(raw) as Tokens;
  // Refresh a few minutes early so a busy front desk is never cut off mid-task.
  if (tokens.expires_at - Date.now() < 5 * 60_000) {
    const refreshed = await refresh(tokens);
    if (!refreshed) {
      clearSession();
      return null;
    }
    tokens = refreshed;
  }
  return tokens.id_token;
}

export function clearSession() {
  sessionStorage.removeItem(TOKENS);
  sessionStorage.removeItem(LAST_ACTIVE);
}

export async function logout(): Promise<void> {
  const cfg = await getConfig();
  const raw = sessionStorage.getItem(TOKENS);
  if (cfg.auth_mode === "local" && raw) {
    await api("/api/auth/logout", { method: "POST", token: JSON.parse(raw).id_token }).catch(() => undefined);
  }
  clearSession();
  if (cfg.auth_mode === "cognito") {
    const params = new URLSearchParams({ client_id: cfg.cognito_client_id, logout_uri: `${window.location.origin}/staff` });
    window.location.assign(`${cfg.cognito_domain}/logout?${params}`);
  } else {
    window.location.assign("/staff");
  }
}
