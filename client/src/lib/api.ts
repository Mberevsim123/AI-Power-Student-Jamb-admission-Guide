import type { AgentState, StreamMetadata, Tokens } from "./types";

const base = (import.meta.env.VITE_API_BASE_URL || "/api/v1").replace(
  /\/$/,
  "",
);
const storageKey = "jamb-guide.session.v1";
let tokens: Tokens | null = null;
try {
  const saved: unknown = JSON.parse(
    sessionStorage.getItem(storageKey) || "null",
  );
  if (
    saved &&
    typeof saved === "object" &&
    "access_token" in saved &&
    "refresh_token" in saved &&
    typeof saved.access_token === "string" &&
    typeof saved.refresh_token === "string"
  )
    tokens = saved as Tokens;
} catch {
  /* A blocked or invalid browser store must not prevent startup. */
}

let sessionVersion = 0;
function storeTokens(next: Tokens | null) {
  tokens = next;
  try {
    if (next) sessionStorage.setItem(storageKey, JSON.stringify(next));
    else sessionStorage.removeItem(storageKey);
  } catch {
    /* Session remains usable in memory when storage is unavailable. */
  }
}
export function setTokens(next: Tokens | null) {
  sessionVersion += 1;
  storeTokens(next);
}
export const hasSession = () => tokens !== null;

export class ApiError extends Error {
  constructor(
    message: string,
    public status = 0,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function responseError(response: Response): Promise<ApiError> {
  let message =
    response.status === 429
      ? "Too many requests. Please wait a moment and try again."
      : response.status >= 500
        ? "The service is temporarily unavailable. Please try again shortly."
        : "That request could not be completed.";
  try {
    const body = await response.json();
    if (typeof body.detail === "string") message = body.detail;
    if (Array.isArray(body.errors))
      message = body.errors
        .map(
          (error: { field?: string; message: string }) =>
            `${error.field?.split(" → ").pop() || "Field"}: ${error.message}`,
        )
        .join(". ");
  } catch {
    /* Proxies may return HTML instead of an API error. */
  }
  return new ApiError(message, response.status);
}

let refreshPromise: Promise<boolean> | null = null;
async function refreshSession() {
  if (refreshPromise) return refreshPromise;
  const session = tokens;
  if (!session) return false;
  refreshPromise = (async () => {
    let response: Response;
    try {
      response = await fetch(`${base}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: session.refresh_token }),
        signal: AbortSignal.timeout(15000),
      });
    } catch {
      throw new ApiError(
        "Could not reconnect. Please check your connection and try again.",
      );
    }
    // A logout or another login may have happened while the request was in flight.
    if (tokens !== session) return false;
    if (!response.ok) {
      if (response.status >= 500 || response.status === 429)
        throw await responseError(response);
      setTokens(null);
      window.dispatchEvent(new Event("jamb:session-expired"));
      return false;
    }
    const refreshed = (await response.json()) as Tokens;
    if (tokens !== session) return false;
    storeTokens(refreshed);
    return true;
  })().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

async function send(
  path: string,
  init: RequestInit = {},
  retry = true,
): Promise<Response> {
  const session = tokens;
  const version = sessionVersion;
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (session) headers.set("Authorization", `Bearer ${session.access_token}`);
  let response: Response;
  try {
    response = await fetch(`${base}${path}`, {
      ...init,
      headers,
      signal: init.signal
        ? AbortSignal.any([
            init.signal,
            AbortSignal.timeout(path === "/chat/stream" ? 120000 : 20000),
          ])
        : AbortSignal.timeout(20000),
    });
  } catch (error) {
    if (init.signal?.aborted) throw error;
    throw new ApiError(
      "We couldn’t connect. Check your connection and try again.",
    );
  }
  if (version !== sessionVersion)
    throw new ApiError("Your account session changed. Please try again.", 401);
  if (
    response.status === 401 &&
    retry &&
    tokens &&
    !path.startsWith("/auth/login")
  ) {
    // A delayed 401 may belong to a token that another request already refreshed.
    // Never refresh or replay an old account's request under a new login.
    if (tokens !== session) {
      return send(path, init, false);
    }
    if (await refreshSession()) return send(path, init, false);
    throw new ApiError("Your session has expired. Please sign in again.", 401);
  }
  if (!response.ok) throw await responseError(response);
  return response;
}

export const api = {
  async get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return (await send(path, { signal })).json();
  },
  async post<T>(path: string, data?: unknown): Promise<T> {
    const response = await send(path, {
      method: "POST",
      body: data === undefined ? undefined : JSON.stringify(data),
    });
    return response.status === 204 ? (undefined as T) : response.json();
  },
  async patch<T>(path: string, data: unknown): Promise<T> {
    return (
      await send(path, { method: "PATCH", body: JSON.stringify(data) })
    ).json();
  },
  async delete(path: string) {
    await send(path, { method: "DELETE" });
  },
  async health(signal?: AbortSignal) {
    const response = await fetch(`${base.replace(/\/api\/v1$/, "")}/health`, {
      signal: signal || AbortSignal.timeout(5000),
    });
    if (!response.ok)
      throw new ApiError("Service unavailable", response.status);
    return response.json();
  },
};

/** Consume SSE by frames, preserving partial UTF-8 characters, CRLF and token newlines. */
export async function readEventStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: string) => void,
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const drain = () => {
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of frame.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:"))
          data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) onEvent(event, data.join("\n"));
    }
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Keep a trailing CR until the next chunk so CRLF split over chunks survives.
      buffer = buffer.replace(/\r\n/g, "\n");
      drain();
    }
    buffer += decoder.decode();
    buffer = buffer.replace(/\r\n/g, "\n");
    drain();
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function streamChat(
  message: string,
  conversationId: number | null,
  signal: AbortSignal,
  onMetadata: (metadata: StreamMetadata) => void,
  onDelta: (text: string) => void,
  onAgent?: (state: AgentState) => void,
) {
  const response = await send("/chat/stream", {
    method: "POST",
    body: JSON.stringify({ message, conversation_id: conversationId }),
    signal,
  });
  if (!response.body)
    throw new ApiError("This browser could not open the response stream.");
  let completed = false;
  await readEventStream(response.body, (event, data) => {
    if (data === "[DONE]") {
      completed = true;
      return;
    }
    let value;
    try {
      value = JSON.parse(data);
    } catch {
      throw new ApiError(
        "We couldn’t read the response. Please reopen this conversation and try again.",
      );
    }
    if (event === "error")
      throw new ApiError(
        value.detail || "The response was interrupted.",
        value.status,
      );
    if (event === "metadata") onMetadata(value);
    else if (event === "agent") onAgent?.(value);
    else if (typeof value.delta === "string") onDelta(value.delta);
  });
  if (!completed)
    throw new ApiError(
      "The response was interrupted. Reopen this conversation to check its saved messages.",
    );
}

export const errorMessage = (error: unknown) =>
  error instanceof Error
    ? error.message
    : "Something went wrong. Please try again.";
export const safeUrl = (value?: string | null) => {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return ["https:", "http:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
};
