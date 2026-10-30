// Fetch-based API client. Reads JWT from localStorage on the client side
// and attaches it to every request. Throws ApiError on non-2xx.

const BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const TOKEN_KEY = "gunivox.jwt";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown) {
    // FastAPI puts the human-readable reason in `detail` — show that alone
    // rather than raw JSON, so error toasts read as a sentence.
    const detail =
      body && typeof body === "object" && typeof (body as { detail?: unknown }).detail === "string"
        ? (body as { detail: string }).detail
        : null;
    super(
      detail ?? `HTTP ${status}: ${typeof body === "string" ? body : JSON.stringify(body)}`,
    );
    this.status = status;
    this.body = body;
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string | null): void {
  if (typeof window === "undefined") return;
  if (token) localStorage.setItem(TOKEN_KEY, token);
  else localStorage.removeItem(TOKEN_KEY);
}

interface RequestOpts extends Omit<RequestInit, "body"> {
  body?: unknown;
  raw?: boolean; // skip JSON parsing (e.g., file/blob)
}

async function request<T = unknown>(path: string, opts: RequestOpts = {}): Promise<T> {
  const headers = new Headers(opts.headers);
  const token = getToken();
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    if (opts.body instanceof FormData) {
      body = opts.body;
    } else if (typeof opts.body === "string") {
      body = opts.body;
      if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    } else {
      body = JSON.stringify(opts.body);
      headers.set("Content-Type", "application/json");
    }
  }

  const res = await fetch(`${BASE_URL}${path}`, { ...opts, headers, body });

  if (!res.ok) {
    let errBody: unknown = await res.text();
    try {
      errBody = JSON.parse(errBody as string);
    } catch {
      /* keep as string */
    }
    if (res.status === 401 && typeof window !== "undefined") {
      // Token invalid/expired — bounce to login
      setToken(null);
      if (!window.location.pathname.startsWith("/login")) {
        window.location.href = "/login";
      }
    }
    throw new ApiError(res.status, errBody);
  }

  if (opts.raw) return res as unknown as T;
  if (res.status === 204) return undefined as unknown as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return res.text() as unknown as T;
}

export const api = {
  base: BASE_URL,
  get: <T = unknown>(path: string) => request<T>(path),
  post: <T = unknown>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body }),
  put: <T = unknown>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body }),
  delete: <T = unknown>(path: string) =>
    request<T>(path, { method: "DELETE" }),
  upload: <T = unknown>(
    path: string,
    file: File,
    extra?: Record<string, string>,
  ) => {
    const fd = new FormData();
    fd.append("file", file);
    if (extra) {
      for (const [k, v] of Object.entries(extra)) fd.append(k, v);
    }
    return request<T>(path, { method: "POST", body: fd });
  },
};

export function recordingUrl(callId: number): string {
  const token = getToken();
  // Audio element can't send Authorization header in <audio src>, so use
  // a one-off blob URL via the API helper (see CallDetail page).
  return `${BASE_URL}/api/calls/${callId}/recording${token ? `?_t=${token}` : ""}`;
}
