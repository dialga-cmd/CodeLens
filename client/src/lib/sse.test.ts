import { describe, expect, it } from "vitest";

import { SSEParser, parseSSE } from "./sse";

/**
 * The exact bytes `sse-starlette` writes: CRLF line endings and a blank CRLF line
 * between events. The bug these tests exist for was that the client split on
 * `"\n\n"`, which this format never contains.
 */
const crlf = (event: string, data: string) => `event: ${event}\r\ndata: ${data}\r\n\r\n`;

describe("SSEParser", () => {
  it("parses CRLF-terminated events, which is what the server actually sends", () => {
    const stream = [
      crlf("sources", '{"sources":[],"tools_used":[]}'),
      crlf("delta", '{"text":"The answer is flask/app.py."}'),
      crlf("done", '{"ok":true}'),
    ];
    expect(parseSSE(stream)).toEqual([
      { event: "sources", data: '{"sources":[],"tools_used":[]}', id: "" },
      { event: "delta", data: '{"text":"The answer is flask/app.py."}', id: "" },
      { event: "done", data: '{"ok":true}', id: "" },
    ]);
  });

  it("parses LF-terminated events", () => {
    const stream = ["event: delta\ndata: {\"text\":\"hi\"}\n\n", "event: done\ndata: {\"ok\":true}\n\n"];
    expect(parseSSE(stream).map((message) => message.event)).toEqual(["delta", "done"]);
    expect(parseSSE(stream)[0].data).toBe('{"text":"hi"}');
  });

  it("reassembles an event split across chunks in the middle of a line", () => {
    const whole = crlf("delta", '{"text":"split in half"}');
    for (let cut = 1; cut < whole.length; cut += 1) {
      const parsed = parseSSE([whole.slice(0, cut), whole.slice(cut)]);
      expect(parsed, `cut at ${cut}`).toEqual([
        { event: "delta", data: '{"text":"split in half"}', id: "" },
      ]);
    }
  });

  it("does not invent an event when a chunk boundary falls inside CRLF", () => {
    // "...\r" | "\n\r\n" - the CR ends the chunk and the LF that makes it a real
    // line ending arrives next. Reading the CR as a blank line here would
    // dispatch half an event and then dispatch an empty one.
    const parsed = parseSSE(['event: delta\r', '\ndata: {"text":"x"}\r', "\n\r\n"]);
    expect(parsed).toEqual([{ event: "delta", data: '{"text":"x"}', id: "" }]);
  });

  it("dispatches a final event that arrives with no trailing blank line", () => {
    const stream = [crlf("delta", '{"text":"first"}'), 'event: done\r\ndata: {"ok":true}'];
    expect(parseSSE(stream)).toEqual([
      { event: "delta", data: '{"text":"first"}', id: "" },
      { event: "done", data: '{"ok":true}', id: "" },
    ]);
  });

  it("joins multi-line data fields with newlines, as the specification says", () => {
    const stream = ["event: delta\r\ndata: line one\r\ndata: line two\r\n\r\n"];
    expect(parseSSE(stream)[0].data).toBe("line one\nline two");
  });

  it("reads several events out of a single chunk", () => {
    const parsed = parseSSE([
      `${crlf("sources", '{"sources":[]}')}${crlf("delta", '{"text":"a"}')}${crlf(
        "delta",
        '{"text":"b"}'
      )}${crlf("done", '{"ok":true}')}`,
    ]);
    expect(parsed.map((message) => [message.event, message.data])).toEqual([
      ["sources", '{"sources":[]}'],
      ["delta", '{"text":"a"}'],
      ["delta", '{"text":"b"}'],
      ["done", '{"ok":true}'],
    ]);
  });

  it("reads a real answer out of a stream split at arbitrary byte offsets", () => {
    // The failure this fixes, end to end: a whole conversation, chopped into
    // 7-byte pieces the way a network would chop it.
    const words = ["This ", "is ", "the ", "answer."];
    const stream = [
      crlf("sources", '{"sources":[{"path":"flask/app.py","source":"context"}]}'),
      ...words.map((word) => crlf("delta", JSON.stringify({ text: word }))),
      crlf("done", '{"ok":true}'),
    ];
    const bytes = new TextEncoder().encode(stream.join(""));
    const chunks: string[] = [];
    for (let index = 0; index < bytes.length; index += 7) {
      chunks.push(new TextDecoder().decode(bytes.slice(index, index + 7)));
    }

    const parsed = parseSSE(chunks);
    const answer = parsed
      .filter((message) => message.event === "delta")
      .map((message) => JSON.parse(message.data).text)
      .join("");
    expect(answer).toBe("This is the answer.");
    expect(parsed.filter((message) => message.event === "sources")).toHaveLength(1);
    expect(parsed.at(-1)?.event).toBe("done");
  });

  it("ignores comments and unknown fields", () => {
    const stream = [": keep-alive\r\n", "retry: 3000\r\n", crlf("delta", '{"text":"kept"}')];
    expect(parseSSE(stream)).toEqual([
      { event: "delta", data: '{"text":"kept"}', id: "" },
    ]);
  });

  it("keeps an event name and an id from earlier lines of the same event", () => {
    const stream = ["event: delta\r\nid: 7\r\ndata: {\"text\":\"x\"}\r\n\r\n"];
    expect(parseSSE(stream)).toEqual([{ event: "delta", data: '{"text":"x"}', id: "7" }]);
  });

  it("drops an event that carried a name but no data", () => {
    expect(parseSSE(["event: ping\r\n\r\n", crlf("done", '{"ok":true}')])).toEqual([
      { event: "done", data: '{"ok":true}', id: "" },
    ]);
  });

  it("names an unnamed event \"message\"", () => {
    expect(parseSSE(["data: plain\r\n\r\n"])).toEqual([
      { event: "message", data: "plain", id: "" },
    ]);
  });
});