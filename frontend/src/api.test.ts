import { describe, it, expect, vi, afterEach } from "vitest";
import { api, streamChat } from "./api";
afterEach(() => vi.unstubAllGlobals());
describe("local API boundary", () => {
  it("handles fragmented SSE and authoritative result", async () => {
    const encoder = new TextEncoder();
    const pieces = [
      'event: token\ndata: {"te',
      'xt":"Hello"}\n\n',
      'event: result\ndata: {"answer":"Hello","conversation_id":"c","citations":[],"warnings":[],"model":"local","graph":{"nodes":[],"edges":[],"highlighted_node_ids":[],"highlighted_edge_ids":[]}}\n\n',
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
    const result = await streamChat("Question", null, token, () => {});
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
