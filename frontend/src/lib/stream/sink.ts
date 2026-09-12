/**
 * The incremental token sink.
 *
 * A fast backend can produce several hundred tokens per second; a display refreshes
 * sixty times per second. Rendering per token therefore does up to ten times the work
 * the screen can show, and on a machine that is *also* running the model that wasted
 * work competes with inference for the same cores.
 *
 * So tokens are appended to a buffer and published once per animation frame, coalesced
 * (ADR-0012). The consumer sees a monotonically growing string and re-renders one text
 * node — never the conversation.
 *
 * `requestAnimationFrame` also stops work entirely while the tab is hidden, which is
 * the correct behaviour: nobody is looking, and the text is still accumulating.
 */

export type SinkListener = (text: string) => void;

/** Scheduler seam, so tests can drive frames deterministically. */
export interface FrameScheduler {
  request(callback: () => void): number;
  cancel(handle: number): void;
}

const defaultScheduler: FrameScheduler = {
  request: (callback) =>
    typeof requestAnimationFrame === "function"
      ? requestAnimationFrame(() => callback())
      : (setTimeout(callback, 16) as unknown as number),
  cancel: (handle) =>
    typeof cancelAnimationFrame === "function"
      ? cancelAnimationFrame(handle)
      : clearTimeout(handle as unknown as ReturnType<typeof setTimeout>),
};

export class TokenSink {
  #parts: string[] = [];
  #published = "";
  #pending = false;
  #handle: number | null = null;
  #listener: SinkListener | null = null;
  readonly #scheduler: FrameScheduler;

  /** Number of frames actually published. Used by tests and the metrics overlay. */
  flushes = 0;

  constructor(scheduler: FrameScheduler = defaultScheduler) {
    this.#scheduler = scheduler;
  }

  /** The text published to the consumer as of the last frame. */
  get text(): string {
    return this.#published;
  }

  /** Everything appended so far, including tokens not yet published. */
  get pendingText(): string {
    return this.#parts.join("");
  }

  /** Subscribe. Only one listener is supported; a sink drives exactly one message. */
  subscribe(listener: SinkListener): void {
    this.#listener = listener;
  }

  /**
   * Append a token. Cheap by construction: one array push and at most one frame
   * request, whatever the token rate.
   */
  push(token: string): void {
    this.#parts.push(token);
    if (this.#pending) return;
    this.#pending = true;
    this.#handle = this.#scheduler.request(() => this.#flush());
  }

  /** Publish immediately, outside the frame cadence. Used when a stream ends. */
  flushNow(): void {
    if (this.#handle !== null) {
      this.#scheduler.cancel(this.#handle);
      this.#handle = null;
    }
    this.#pending = false;
    this.#publish();
  }

  /** Cancel any scheduled frame and drop the listener. */
  dispose(): void {
    if (this.#handle !== null) {
      this.#scheduler.cancel(this.#handle);
      this.#handle = null;
    }
    this.#pending = false;
    this.#listener = null;
  }

  /** Replace the contents, for example when a stored message is reloaded. */
  reset(text = ""): void {
    this.#parts = text ? [text] : [];
    this.#published = text;
    this.flushes = 0;
  }

  #flush(): void {
    this.#handle = null;
    this.#pending = false;
    this.#publish();
  }

  #publish(): void {
    const next = this.#parts.join("");
    if (next === this.#published) return;
    this.#published = next;
    this.flushes += 1;
    this.#listener?.(next);
  }
}
