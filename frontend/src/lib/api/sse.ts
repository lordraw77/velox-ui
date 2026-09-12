/**
 * A server-sent event reader built on `fetch`.
 *
 * `EventSource` is not usable here: it cannot issue a POST, cannot set an
 * `Authorization` header, and cannot be aborted cleanly. So the stream is read from
 * the `fetch` body directly.
 *
 * The parser is written against the byte stream rather than assuming chunk boundaries
 * line up with frames. They do not: a token can be split across two network chunks,
 * and a naive `split("\n\n")` per chunk drops or duplicates text exactly when
 * generation is fastest.
 */

/** One parsed event. `event` is the SSE event name; comments are not surfaced. */
export interface SseEvent {
  event: string;
  data: string;
}

/**
 * Read an SSE response body as a sequence of events.
 *
 * @param body - The response body stream.
 * @param signal - Abort signal; aborting stops the iteration and releases the reader.
 * @yields Each complete event, in order.
 */
export async function* readSse(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) return;
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      // Frames are separated by a blank line. Anything after the last separator is a
      // partial frame and stays in the buffer until the rest arrives.
      let separator = buffer.indexOf("\n\n");
      while (separator !== -1) {
        const frame = buffer.slice(0, separator);
        buffer = buffer.slice(separator + 2);
        const parsed = parseFrame(frame);
        if (parsed) yield parsed;
        separator = buffer.indexOf("\n\n");
      }
    }

    const trailing = parseFrame(buffer);
    if (trailing) yield trailing;
  } finally {
    reader.releaseLock();
  }
}

/**
 * Parse one SSE frame.
 *
 * @param frame - The frame text, without its trailing blank line.
 * @returns The event, or `null` for a comment or an empty frame.
 */
export function parseFrame(frame: string): SseEvent | null {
  let event = "message";
  const data: string[] = [];

  for (const line of frame.split("\n")) {
    if (line === "" || line.startsWith(":")) continue; // heartbeat comment
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    // A single optional space after the colon is part of the framing, not the value.
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  if (data.length === 0) return null;
  return { event, data: data.join("\n") };
}
