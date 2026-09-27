/**
 * The single HTTP entry point for the whole frontend.
 *
 * Three decisions worth stating, because every page depends on them:
 *
 * 1. The base URL is a *relative* `/api/v1`. Next rewrites it to FastAPI (see
 *    next.config.ts), which makes every call same-origin. That is what lets the
 *    refresh cookie be `SameSite=Lax` instead of `None` — a cross-site cookie
 *    would need `Secure` + `None` and would be dropped by default in most
 *    browsers the moment this runs anywhere other than https.
 *
 * 2. The access token lives in memory only. Nothing is written to
 *    localStorage, so an XSS cannot read it out of storage after the fact, and
 *    a page reload re-mints one from the httpOnly refresh cookie. The cost is
 *    one silent refresh at boot; the benefit is that the durable credential is
 *    never reachable from JavaScript.
 *
 * 3. A 401 triggers exactly one shared refresh, not one per in-flight request.
 *    A dashboard fires eight calls at once; without the shared promise, an
 *    expired token produces eight refreshes, seven of which race and rotate the
 *    cookie out from under the other six.
 */

import type { Problem } from "@/types/api";

export const API_BASE = "/api/v1";

/* ------------------------------------------------------------ token store -- */

let accessToken: string | null = null;
let onUnauthenticated: (() => void) | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** Registered by the auth provider so a dead session can clear app state. */
export function setUnauthenticatedHandler(fn: (() => void) | null): void {
  onUnauthenticated = fn;
}

/* ----------------------------------------------------------------- errors -- */

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;

  constructor(status: number, problem: Problem) {
    super(problem.detail || problem.title || `Request failed (${status})`);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }

  /** Field-level messages, shaped for react-hook-form's setError. */
  get fieldErrors(): Record<string, string> {
    const out: Record<string, string> = {};
    // `problem.errors` is an object keyed by field name, not an array of pairs.
    // Iterating it with `for...of` throws "object is not iterable" *inside the
    // error handler*, which swallows the real validation message and replaces it
    // with a TypeError — so this shape has to match the API exactly.
    for (const [field, messages] of Object.entries(this.problem.errors ?? {})) {
      // `setError` renders a single string, so show the first failure and leave
      // the rest on `problem.errors` for anyone who wants to list them all.
      // Guarded rather than indexed blind: the server types this payload as
      // `dict[str, Any]`, so a bare string here would otherwise surface as its
      // own first character.
      const first = Array.isArray(messages) ? messages[0] : messages;
      if (typeof first === "string") out[field] = first;
    }
    return out;
  }

  get isAuth(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

async function toProblem(res: Response): Promise<Problem> {
  // The API speaks RFC 9457 everywhere, but a proxy error or a crash before the
  // handler can still hand back HTML. Falling through to a synthetic problem
  // keeps the error path uniform rather than throwing inside the error handler.
  try {
    const body = await res.json();
    if (body && typeof body === "object" && "title" in body) {
      return { status: res.status, type: "about:blank", ...body } as Problem;
    }
    return {
      type: "about:blank",
      title: res.statusText || "Request failed",
      status: res.status,
      detail: typeof body === "string" ? body : undefined,
    };
  } catch {
    return {
      type: "about:blank",
      title: res.statusText || "Request failed",
      status: res.status,
      detail:
        res.status >= 500
          ? "The server could not complete the request. Please try again."
          : undefined,
    };
  }
}

/* ---------------------------------------------------------------- refresh -- */

let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      if (!res.ok) return null;
      const data = (await res.json()) as { access_token?: string };
      accessToken = data.access_token ?? null;
      return accessToken;
    } catch {
      return null;
    } finally {
      // Cleared in a microtask so callers that awaited this promise all see the
      // same result before a fresh refresh can start.
      setTimeout(() => {
        refreshInFlight = null;
      }, 0);
    }
  })();

  return refreshInFlight;
}

/** Exposed so the auth provider can restore a session on first paint. */
export async function bootstrapSession(): Promise<string | null> {
  return refreshAccessToken();
}

/* ---------------------------------------------------------------- request -- */

export interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
  /** Query parameters; null/undefined/"" entries are dropped. */
  query?: Record<string, string | number | boolean | null | undefined | string[]>;
  /** Set false for the auth endpoints themselves, to avoid a refresh loop. */
  retryOnAuthFailure?: boolean;
  /** Skip the Authorization header entirely (login, register, reset). */
  anonymous?: boolean;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return url;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      for (const v of value) if (v !== "") params.append(key, String(v));
    } else {
      params.append(key, String(value));
    }
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function send<T>(path: string, options: RequestOptions, token: string | null): Promise<Response> {
  const headers = new Headers(options.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  }
  if (token && !options.anonymous) headers.set("Authorization", `Bearer ${token}`);

  return fetch(buildUrl(path, options.query), {
    ...options,
    headers,
    credentials: "include",
    body:
      options.body === undefined
        ? undefined
        : options.body instanceof FormData
          ? options.body
          : JSON.stringify(options.body),
  });
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const retry = options.retryOnAuthFailure ?? !options.anonymous;

  let res = await send<T>(path, options, accessToken);

  if (res.status === 401 && retry) {
    const fresh = await refreshAccessToken();
    if (fresh) {
      res = await send<T>(path, options, fresh);
    } else {
      accessToken = null;
      onUnauthenticated?.();
    }
  }

  if (!res.ok) throw new ApiError(res.status, await toProblem(res));

  if (res.status === 204) return undefined as T;

  const text = await res.text();
  if (!text) return undefined as T;
  return JSON.parse(text) as T;
}

export const api = {
  get: <T>(path: string, options: RequestOptions = {}) =>
    request<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options: RequestOptions = {}) =>
    request<T>(path, { ...options, method: "POST", body }),
  put: <T>(path: string, body?: unknown, options: RequestOptions = {}) =>
    request<T>(path, { ...options, method: "PUT", body }),
  patch: <T>(path: string, body?: unknown, options: RequestOptions = {}) =>
    request<T>(path, { ...options, method: "PATCH", body }),
  delete: <T>(path: string, options: RequestOptions = {}) =>
    request<T>(path, { ...options, method: "DELETE" }),
};

/** Human-readable message for any thrown value, for toasts and error panels. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "Something went wrong.";
}
