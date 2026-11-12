/** HTTP-клиент: конверт {"success", "data", "error"} (§7) */

export class ApiError extends Error {
  code: string;
  status: number;

  constructor(message: string, code: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

interface Options {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined>;
}

export async function api<T>(path: string, opts: Options = {}): Promise<T> {
  const url = new URL(path, window.location.origin);
  if (opts.params) {
    for (const [k, v] of Object.entries(opts.params)) {
      if (v !== undefined && v !== "") url.searchParams.set(k, String(v));
    }
  }
  let resp: Response;
  try {
    resp = await fetch(url.toString(), {
      method: opts.method ?? "GET",
      headers: opts.body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    throw new ApiError("Сервер недоступен", "network_error", 0);
  }
  const text = await resp.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    throw new ApiError(text.slice(0, 200) || `HTTP ${resp.status}`, "parse_error", resp.status);
  }
  const obj = json as { success?: boolean; data?: T; error?: { code?: string; message?: string }; detail?: string };
  if (!resp.ok || obj.success === false) {
    throw new ApiError(
      obj.error?.message ?? obj.detail ?? `HTTP ${resp.status}`,
      obj.error?.code ?? "error",
      resp.status,
    );
  }
  return obj.data as T;
}

export async function revealKey(kind: "provider-keys" | "keys", id: number): Promise<string> {
  const data = await api<{ key: string }>(`/api/${kind}/${id}/reveal`);
  return data.key;
}
