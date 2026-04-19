const TOKEN_KEY = "lexi_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
}

async function parseError(r: Response): Promise<string> {
  try {
    const j = await r.json();
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) return j.detail.map((x: { msg?: string }) => x.msg).join("; ");
  } catch {
    /* ignore */
  }
  return r.statusText || "Request failed";
}

export async function api<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {}
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.headers as Record<string, string>),
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const { json: jsonBody, ...rest } = init;
  let body: BodyInit | undefined = rest.body ?? undefined;
  if (jsonBody !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(jsonBody);
  }

  const r = await fetch(`/api${path}`, { ...rest, headers, body });
  if (r.status === 204) return undefined as T;
  if (!r.ok) throw new ApiError(r.status, await parseError(r));
  if (r.headers.get("content-length") === "0") return undefined as T;
  const ct = r.headers.get("content-type");
  if (ct && ct.includes("application/json")) return (await r.json()) as T;
  return undefined as T;
}
