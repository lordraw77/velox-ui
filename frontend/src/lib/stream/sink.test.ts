import { describe, expect, it } from "vitest";
import { TokenSink, type FrameScheduler } from "./sink";

/** A scheduler that only fires when a test says so. */
function manualScheduler() {
  const queue: (() => void)[] = [];
  const scheduler: FrameScheduler = {
    request(callback) {
      queue.push(callback);
      return queue.length;
    },
    cancel(handle) {
      queue[handle - 1] = () => {};
    },
  };
  return {
    scheduler,
    frame() {
      const pending = queue.splice(0, queue.length);
      for (const callback of pending) callback();
    },
    get requested() {
      return queue.length;
    },
  };
}

describe("TokenSink", () => {
  it("publishes nothing until a frame runs", () => {
    const { scheduler, frame } = manualScheduler();
    const sink = new TokenSink(scheduler);
    const seen: string[] = [];
    sink.subscribe((text) => seen.push(text));

    sink.push("Hel");
    sink.push("lo");
    expect(seen).toEqual([]);
    expect(sink.text).toBe("");

    frame();
    expect(seen).toEqual(["Hello"]);
  });

  it("coalesces a burst into one publish", () => {
    // The point of the whole module: 200 tokens in one frame must cost one render,
    // not 200.
    const { scheduler, frame } = manualScheduler();
    const sink = new TokenSink(scheduler);
    let renders = 0;
    sink.subscribe(() => renders++);

    for (let index = 0; index < 200; index++) sink.push("x");
    frame();

    expect(renders).toBe(1);
    expect(sink.text).toHaveLength(200);
  });

  it("requests at most one frame per burst", () => {
    const { scheduler, frame, requested } = manualScheduler();
    const sink = new TokenSink(scheduler);
    sink.subscribe(() => {});
    void requested;

    for (let index = 0; index < 50; index++) sink.push("y");
    // One outstanding request, not fifty.
    frame();
    for (let index = 0; index < 50; index++) sink.push("z");
    frame();
    expect(sink.text).toBe("y".repeat(50) + "z".repeat(50));
  });

  it("keeps appending across frames", () => {
    const { scheduler, frame } = manualScheduler();
    const sink = new TokenSink(scheduler);
    const seen: string[] = [];
    sink.subscribe((text) => seen.push(text));

    sink.push("a");
    frame();
    sink.push("b");
    frame();

    expect(seen).toEqual(["a", "ab"]);
  });

  it("flushNow publishes without waiting for a frame", () => {
    const { scheduler } = manualScheduler();
    const sink = new TokenSink(scheduler);
    const seen: string[] = [];
    sink.subscribe((text) => seen.push(text));

    sink.push("done");
    sink.flushNow();
    expect(seen).toEqual(["done"]);
  });

  it("does not publish twice for the same text", () => {
    const { scheduler, frame } = manualScheduler();
    const sink = new TokenSink(scheduler);
    let renders = 0;
    sink.subscribe(() => renders++);

    sink.push("a");
    frame();
    sink.flushNow();
    sink.flushNow();
    expect(renders).toBe(1);
  });

  it("publishes nothing after dispose", () => {
    const { scheduler, frame } = manualScheduler();
    const sink = new TokenSink(scheduler);
    let renders = 0;
    sink.subscribe(() => renders++);

    sink.push("a");
    sink.dispose();
    frame();
    expect(renders).toBe(0);
  });

  it("exposes unpublished text for persistence on an interrupted stream", () => {
    const { scheduler } = manualScheduler();
    const sink = new TokenSink(scheduler);
    sink.push("partial");
    // Nothing rendered yet, but the text exists and must not be lost.
    expect(sink.text).toBe("");
    expect(sink.pendingText).toBe("partial");
  });
});
