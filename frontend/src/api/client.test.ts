import { describe, it, expect, vi, afterEach } from "vitest";
import { api, streamChat } from "./client";
afterEach(() => vi.unstubAllGlobals());
describe("local API boundary", () => {
  it("handles fragmented SSE and authoritative result", async () => {
    const encoder = new TextEncoder();
    const pieces = [
      'event: draft\ndata: {"text":"Hel"}\n\n',
      'event: token\ndata: {"te',
      'xt":"Hello"}\n\n',
      'event: result\ndata: {"answer":"Hello","conversation_id":"c","citations":[],"warnings":[],"model":"local","statements":[],"trace":{"search_text":"Question","rewritten":false,"corrections":{},"intent":"lookup","counts":{"dense":1,"sparse":1,"candidates":1},"reranked":false,"bridge_terms":[],"passages":[]}}\n\n',
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          new ReadableStream({
            start(c) {
              pieces.forEach((p) => c.enqueue(encoder.encode(p)));
              c.close();
            },
          }),
        ),
      ),
    );
    const token = vi.fn();
    const draft = vi.fn();
    const result = await streamChat(
      "Question",
      null,
      token,
      () => {},
      undefined,
      [],
      undefined,
      draft,
    );
    expect(draft).toHaveBeenCalledWith("Hel");
    expect(token).toHaveBeenCalledWith("Hello");
    expect(result.conversation_id).toBe("c");
  });
  it("surfaces model errors and incomplete streams", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response(
            'event: error\ndata: {"message":"Model unavailable"}\n\n',
          ),
        ),
    );
    await expect(
      streamChat(
        "Q",
        null,
        () => {},
        () => {},
      ),
    ).rejects.toThrow("Model unavailable");
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response('event: token\ndata: {"text":"partial"}\n\n'),
        ),
    );
    await expect(
      streamChat(
        "Q",
        null,
        () => {},
        () => {},
      ),
    ).rejects.toThrow("before a final answer");
  });
  it("preserves actionable HTTP errors", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(
          new Response('{"detail":"Upload exceeds 20 MB"}', { status: 413 }),
        ),
    );
    await expect(api.documents()).rejects.toThrow("Upload exceeds 20 MB");
  });
});
describe("answer language", () => {
  const done =
    'event: result\ndata: {"answer":"A","conversation_id":"c","citations":[],"warnings":[],"model":"m","statements":[],"trace":{"search_text":"q","rewritten":false,"corrections":{},"intent":"lookup","counts":{"dense":1,"sparse":1,"candidates":1},"reranked":false,"bridge_terms":[],"passages":[]}}\n\n';
  it("is sent only when one is chosen", async () => {
    const fetch = vi.fn(async () => new Response(done));
    vi.stubGlobal("fetch", fetch);
    const body = (call: number) =>
      JSON.parse(
        (fetch.mock.calls[call] as unknown as [string, RequestInit])[1]
          .body as string,
      );
    await streamChat(
      "q",
      null,
      () => {},
      () => {},
    );
    expect(body(0)).not.toHaveProperty("answer_language");
    await streamChat(
      "q",
      null,
      () => {},
      () => {},
      undefined,
      [],
      undefined,
      undefined,
      "Telugu",
    );
    expect(body(1).answer_language).toBe("Telugu");
  });
});
