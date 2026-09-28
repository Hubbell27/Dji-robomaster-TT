import type { FieldError } from "./types";

export class ApiError extends Error {
  status: number;
  errors: FieldError[];
  constructor(status: number, message: string, errors: FieldError[] = []) {
    super(message);
    this.status = status;
    this.errors = errors;
  }
}

type Opts = { method?: string; body?: unknown; token?: string | null; form?: FormData };

async function raw(path: string, { method = "GET", body, token, form }: Opts = {}): Promise<Response> {
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method,
    headers,
    body: form ?? (body !== undefined ? JSON.stringify(body) : undefined),
    credentials: "omit",
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = res.statusText;
    let errors: FieldError[] = [];
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : detail;
      errors = Array.isArray(data.errors) ? data.errors : [];
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, errors);
  }
  return res;
}

export async function api<T>(path: string, opts: Opts = {}): Promise<T> {
  const res = await raw(path, opts);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export async function apiBlob(path: string, token: string | null): Promise<Blob> {
  return (await raw(path, { token })).blob();
}
