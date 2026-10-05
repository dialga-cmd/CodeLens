/**
 * A Server-Sent Events reader.
 *
 * This exists because of a bug that only ever showed up in a browser: the server
 * sends events terminated with CRLF (that is what `sse-starlette` writes, and what
 * the SSE specification asks for), the client split the stream on `"\n\n"`, so
 * that split never matched, no event was ever parsed, and every question came
 * back as "the model returned an empty answer". A test that printed the raw
 * bytes could not see it, because the bytes were correct.
 *
 * So the reader does not assume a line ending. It accepts CRLF, LF and a lone
 * CR, and it does not decide anything at a chunk boundary: a chunk that ends on
 * `\r` may be the first half of a `\r\n`, and the parser cannot know until the
 * next chunk arrives, so it waits.
 */

/** One dispatched event. */
export interface SSEMessage {
  /** The `event:` name. `"message"` when the stream did not name the event. */
  event: string;
  /** Every `data:` line, joined with a newline, as the specification requires. */
  data: string;
  /** The last `id:` seen, or `""`. */
  id: string;
}

export class SSEParser {
  private buffer = "";
  private event = "";
  private data: string[] = [];
  private id = "";

  /**
   * Feed the next chunk of decoded text, and take whatever whole events it
   * completed. A partial trailing event is held back until the rest arrives.
   */
  push(chunk: string): SSEMessage[] {
    this.buffer += chunk;
    const messages: SSEMessage[] = [];

    let start = 0;
    for (let index = 0; index < this.buffer.length; index += 1) {
      const character = this.buffer[index];
      if (character !== "\n" && character !== "\r") continue;

      // A chunk boundary in the middle of CRLF: keep the CR in the buffer and
      // wait. Treating it as a bare CR here would invent a blank line, and with
      // it a phantom event, on the last chunk of a stream.
      if (character === "\r" && index === this.buffer.length - 1) break;

      const line = this.buffer.slice(start, index);
      let next = index + 1;
      if (character === "\r" && this.buffer[next] === "\n") next += 1;

      const message = this.line(line);
      if (message) messages.push(message);
      start = next;
      index = next - 1;
    }

    this.buffer = this.buffer.slice(start);
    return messages;
  }

  /**
   * The stream ended. Dispatch an event that arrived without its closing blank
   * line, which a server that closes the connection straight after writing is
   * entitled to do, and drop the rest.
   */
  flush(): SSEMessage[] {
    const trailing = this.buffer;
    this.buffer = "";
    const messages: SSEMessage[] = [];

    for (const line of trailing.split(/\r\n|\r|\n/)) {
      const message = this.line(line);
      if (message) messages.push(message);
    }
    const last = this.dispatch();
    if (last) messages.push(last);
    return messages;
  }

  /** One line of the stream. Returns an event when the line closes one. */
  private line(line: string): SSEMessage | null {
    // A blank line ends the event, and is the only thing that does.
    if (line === "") return this.dispatch();
    // A leading colon is a comment, and comments keep-alive connections open.
    if (line.startsWith(":")) return null;

    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    // A single leading space after the colon is part of the framing, not data.
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    switch (field) {
      case "event":
        this.event = value;
        return null;
      case "data":
        this.data.push(value);
        return null;
      case "id":
        this.id = value;
        return null;
      default:
        // Unknown fields are ignored, as the specification requires, so that a
        // server adding one does not break an older reader.
        return null;
    }
  }

  /** Hand over the accumulated event and start a new one. */
  private dispatch(): SSEMessage | null {
    if (this.data.length === 0) {
      this.event = "";
      return null;
    }
    const message: SSEMessage = {
      event: this.event || "message",
      data: this.data.join("\n"),
      id: this.id,
    };
    this.event = "";
    this.data = [];
    return message;
  }
}

/**
 * Parse a whole stream given as chunks. Convenience over {@link SSEParser} for
 * callers that already hold the text, and the shape the tests exercise.
 */
export function parseSSE(chunks: readonly string[]): SSEMessage[] {
  const parser = new SSEParser();
  return chunks.flatMap((chunk) => parser.push(chunk)).concat(parser.flush());
}