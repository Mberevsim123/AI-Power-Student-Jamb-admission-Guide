import { afterEach, describe, expect, it, vi } from "vitest";
import {
  api,
  ApiError,
  hasSession,
  readEventStream,
  safeUrl,
  setTokens,
  streamChat,
} from "../src/lib/api";

const encode = new TextEncoder();
function stream(parts: string[]) {
  const bytes = encode.encode(parts.join(""));
  return new ReadableStream<Uint8Array>({
    start(controller) {
      // Single-byte delivery exercises fragmented delimiters and UTF-8 codepoints.
      bytes.forEach((byte) => controller.enqueue(Uint8Array.of(byte)));
      controller.close();
    },
  });
}
afterEach(() => {
  vi.unstubAllGlobals();
  setTokens(null);
});

describe("stream protocol", () => {
  it("preserves UTF-8, CRLF boundaries and multiline SSE data", async () => {
    const events: [string, string][] = [];
    await readEventStream(
      stream([
        ': heartbeat\r\n\r\nevent: metadata\r\ndata: {"conversation_id":1}\r\n\r\n',
        "data: one\r\ndata: two ₦\r\n\r\n",
        "data: [DONE]\r\n\r\n",
      ]),
      (event, data) => events.push([event, data]),
    );
    expect(events).toEqual([
      ["metadata", '{"conversation_id":1}'],
      ["message", "one\ntwo ₦"],
      ["message", "[DONE]"],
    ]);
  });
  it("reports an in-stream provider error instead of claiming success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            stream([
              'event: error\ndata: {"detail":"Provider limit reached","status":429}\n\n',
            ]),
            { status: 200 },
          ),
      ),
    );
    await expect(
      streamChat("test", null, new AbortController().signal, vi.fn(), vi.fn()),
    ).rejects.toMatchObject({ status: 429, message: "Provider limit reached" });
  });
  it("rejects an incomplete stream so a dropped response is recoverable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(stream(['data: {"delta":"partial response"}\n\n'])),
      ),
    );
    await expect(
      streamChat("test", null, new AbortController().signal, vi.fn(), vi.fn()),
    ).rejects.toBeInstanceOf(ApiError);
  });
  it("delivers metadata and text exactly once on success", async () => {
    const metadata = vi.fn(),
      delta = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            stream([
              'event: metadata\ndata: {"conversation_id":9,"message_id":11}\n\n',
              'data: {"delta":"Hello\\nthere"}\n\n',
              "data: [DONE]\n\n",
            ]),
          ),
      ),
    );
    await streamChat(
      "hello",
      null,
      new AbortController().signal,
      metadata,
      delta,
    );
    expect(metadata).toHaveBeenCalledExactlyOnceWith({
      conversation_id: 9,
      message_id: 11,
    });
    expect(delta).toHaveBeenCalledExactlyOnceWith("Hello\nthere");
  });

  it("routes agent frames to their handler, never into the answer text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            stream([
              'event: metadata\ndata: {"conversation_id":1,"message_id":2}\n\n',
              'event: agent\ndata: {"intent":"cutoff_lookup","suggestions":["Option A"]}\n\n',
              'data: {"delta":"Answer"}\n\n',
              "data: [DONE]\n\n",
            ]),
          ),
      ),
    );
    const delta = vi.fn();
    const agent = vi.fn();
    await streamChat("q", null, new AbortController().signal, vi.fn(), delta, agent);
    expect(agent).toHaveBeenCalledExactlyOnceWith({
      intent: "cutoff_lookup",
      suggestions: ["Option A"],
    });
    expect(delta).toHaveBeenCalledExactlyOnceWith("Answer");
  });
});

describe("authentication and errors", () => {
  it("retries a delayed old-token 401 without rotating twice", async () => {
    setTokens({ access_token: "old", refresh_token: "refresh" });
    let release!: () => void;
    const delayed = new Promise<void>((resolve) => {
      release = resolve;
    });
    let refreshes = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit) => {
        if (path.endsWith("/auth/refresh")) {
          refreshes++;
          return Response.json({
            access_token: "new",
            refresh_token: "rotated",
          });
        }
        if (new Headers(init.headers).get("Authorization") === "Bearer new")
          return Response.json({ ok: true });
        if (path.endsWith("/slow")) await delayed;
        return Response.json({}, { status: 401 });
      }),
    );
    const slow = api.get("/slow");
    await api.get("/auth/me");
    release();
    await expect(slow).resolves.toEqual({ ok: true });
    expect(refreshes).toBe(1);
  });
  it("discards in-flight responses after switching accounts", async () => {
    setTokens({ access_token: "first", refresh_token: "first" });
    let release!: (response: Response) => void;
    const fetch = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          release = resolve;
        }),
    );
    vi.stubGlobal("fetch", fetch);
    const pending = api.get("/auth/me");
    setTokens({ access_token: "second", refresh_token: "second" });
    release(Response.json({ id: 1 }));
    await expect(pending).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledOnce();
  });
  it("shares one refresh request when two authenticated requests expire together", async () => {
    setTokens({ access_token: "old-access", refresh_token: "old-refresh" });
    let refreshes = 0;
    const requests = vi.fn(async (path: string, init: RequestInit) => {
      if (path.endsWith("/auth/refresh")) {
        refreshes++;
        await Promise.resolve();
        return Response.json({
          access_token: "new-access",
          refresh_token: "new-refresh",
        });
      }
      return new Headers(init.headers).get("Authorization") ===
        "Bearer new-access"
        ? Response.json({ ok: true })
        : Response.json({ detail: "expired" }, { status: 401 });
    });
    vi.stubGlobal("fetch", requests);
    const responses = await Promise.all([
      api.get("/chat/conversations"),
      api.get("/auth/me"),
    ]);
    expect(refreshes).toBe(1);
    expect(responses).toEqual([{ ok: true }, { ok: true }]);
    expect(hasSession()).toBe(true);
  });
  it("retains the session when refresh fails because the network is down", async () => {
    setTokens({ access_token: "access", refresh_token: "refresh" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) => {
        if (path.endsWith("/auth/refresh")) throw new TypeError("offline");
        return Response.json({}, { status: 401 });
      }),
    );
    await expect(api.get("/auth/me")).rejects.toThrow("Could not reconnect");
    expect(hasSession()).toBe(true);
  });
  it("sanitizes non-JSON server errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("<html>internal proxy error</html>", { status: 502 }),
      ),
    );
    await expect(api.get("/admissions/universities")).rejects.toMatchObject({
      status: 502,
      message:
        "The service is temporarily unavailable. Please try again shortly.",
    });
  });
  it("clears an invalid refresh token and notifies the UI", async () => {
    const dispatchEvent = vi.fn();
    vi.stubGlobal("window", { dispatchEvent });
    setTokens({ access_token: "access", refresh_token: "invalid" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        Response.json({ detail: "invalid token" }, { status: 401 }),
      ),
    );
    await expect(api.get("/auth/me")).rejects.toMatchObject({ status: 401 });
    expect(hasSession()).toBe(false);
    expect(dispatchEvent).toHaveBeenCalledOnce();
  });
});

it("does not turn unsafe source URLs into clickable links", () => {
  expect(safeUrl("javascript:alert(1)")).toBeUndefined();
  expect(safeUrl("data:text/html,malicious")).toBeUndefined();
  expect(safeUrl("https://www.jamb.gov.ng/")).toBe("https://www.jamb.gov.ng/");
});
