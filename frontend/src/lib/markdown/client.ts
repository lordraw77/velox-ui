/**
 * The main-thread side of the markdown worker.
 *
 * One worker serves the whole application. Requests carry a monotonic sequence number
 * per message, and a reply whose sequence is older than what has already been applied
 * is dropped: while a reply is in flight more tokens arrive and a newer request is
 * sent, and worker replies are not guaranteed to come back in order. Without that
 * check a message would visibly flicker backwards.
 *
 * The worker is created on first use. A conversation that is still loading has nothing
 * to render, and spawning a worker plus its markdown bundle before then would spend
 * startup time on work that is not needed yet.
 */

import type { RenderRequest, RenderResponse } from "./protocol";

export type RenderHandler = (html: string, tail: string) => void;

interface Pending {
  handler: RenderHandler;
  applied: number;
}

class MarkdownRenderer {
  #worker: Worker | null = null;
  #pending = new Map<string, Pending>();
  #sequence = 0;

  #ensureWorker(): Worker {
    this.#worker ??= (() => {
      const worker = new Worker(new URL("./worker.ts", import.meta.url), { type: "module" });
      worker.onmessage = (event: MessageEvent<RenderResponse>) => this.#receive(event.data);
      return worker;
    })();
    return this.#worker;
  }

  #receive(response: RenderResponse): void {
    const entry = this.#pending.get(response.id);
    if (!entry) return;
    // A reply that lost a race with a newer one must not be applied.
    if (response.seq < entry.applied) return;
    entry.applied = response.seq;
    entry.handler(response.html, response.tail);
  }

  /**
   * Request a render.
   *
   * @param id - Stable identifier for the thing being rendered, normally a message id.
   * @param text - The markdown source as it currently stands.
   * @param complete - Whether the stream producing it has finished.
   * @param handler - Called with the rendered HTML and the unformatted tail.
   */
  request(id: string, text: string, complete: boolean, handler: RenderHandler): void {
    const existing = this.#pending.get(id);
    if (existing) existing.handler = handler;
    else this.#pending.set(id, { handler, applied: -1 });

    const message: RenderRequest = { id, text, complete, seq: ++this.#sequence };
    this.#ensureWorker().postMessage(message);
  }

  /** Stop tracking a message, for instance when it scrolls out of the rendered window. */
  release(id: string): void {
    this.#pending.delete(id);
  }

  /** Tear the worker down. Used by tests and on navigation away. */
  dispose(): void {
    this.#worker?.terminate();
    this.#worker = null;
    this.#pending.clear();
  }
}

export const markdown = new MarkdownRenderer();
