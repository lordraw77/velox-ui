import { describe, expect, it } from "vitest";
import { VirtualList } from "./list";

describe("VirtualList", () => {
  it("uses the estimate before anything is measured", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(10);
    expect(list.totalHeight).toBe(1000);
    expect(list.offsetOf(3)).toBe(300);
  });

  it("renders only the visible window plus overscan", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 2 });
    list.setCount(1000);
    const view = list.windowFor(5000, 500);

    // Items 50..54 are visible; overscan adds two either side.
    expect(view.start).toBe(48);
    expect(view.end).toBe(57);
    expect(view.offsetTop).toBe(4800);
    // A thousand messages, nine rendered.
    expect(view.end - view.start).toBeLessThan(12);
  });

  it("handles items of wildly different heights", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(4);
    list.measure(0, 40); // one-word reply
    list.measure(1, 800); // long code block
    list.measure(2, 40);
    list.measure(3, 40);

    expect(list.totalHeight).toBe(920);
    expect(list.offsetOf(2)).toBe(840);

    const view = list.windowFor(500, 100);
    expect(view.start).toBe(1);
  });

  it("reports the scroll delta when an item above the viewport is measured", () => {
    // This is the anchoring contract. Measuring an off-screen item taller than
    // estimated must tell the caller how far to move scrollTop, or the text the
    // reader is looking at jumps.
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(20);
    const delta = list.measure(0, 340);
    expect(delta).toBe(240);
    expect(list.offsetOf(1)).toBe(340);
  });

  it("reports no delta when a measurement confirms the estimate", () => {
    // The delta is the *shift* a measurement introduces, not the height. An item that
    // turns out to be exactly as tall as assumed moves nothing, so there is nothing
    // for the caller to correct.
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(5);
    expect(list.measure(2, 100)).toBe(0);
    expect(list.measure(2, 100)).toBe(0);
    expect(list.isMeasured(2)).toBe(true);
  });

  it("reports the delta when a measurement contradicts the estimate", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(5);
    expect(list.measure(2, 160)).toBe(60);
    expect(list.measure(2, 120)).toBe(-40);
  });

  it("refines the estimate from what has been measured", () => {
    // A conversation of long code blocks must not keep guessing 96 pixels, or the
    // scrollbar stays wrong and every scroll corrects with a visible jump.
    const list = new VirtualList({ estimatedItemHeight: 96, overscan: 0 });
    list.setCount(100);
    expect(list.estimate).toBe(96);

    list.measure(0, 500);
    list.measure(1, 500);
    expect(list.estimate).toBe(500);

    // Unmeasured items now use the better figure.
    expect(list.heightOf(50)).toBe(96); // already allocated at the old estimate
    list.setCount(101);
    expect(list.heightOf(100)).toBe(500);
  });

  it("keeps measurements when the count grows", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(3);
    list.measure(1, 250);
    list.setCount(5);

    expect(list.isMeasured(1)).toBe(true);
    expect(list.heightOf(1)).toBe(250);
    expect(list.count).toBe(5);
  });

  it("forgets measurements for items that are removed", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(4);
    list.measure(3, 900);
    expect(list.estimate).toBe(900);

    list.setCount(2);
    // The outlier is gone and must not keep skewing the estimate.
    expect(list.estimate).toBe(100);
    expect(list.count).toBe(2);
  });

  it("computes the bottom scroll position", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(10);
    expect(list.bottomScrollTop(400)).toBe(600);
  });

  it("clamps the bottom position when content is shorter than the viewport", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(2);
    expect(list.bottomScrollTop(800)).toBe(0);
  });

  it("treats near-the-end as at the bottom", () => {
    // Streaming grows the content between frames, so an equality check would report
    // "not at the bottom" constantly and stop auto-scrolling.
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(10);
    expect(list.isAtBottom(600, 400)).toBe(true);
    expect(list.isAtBottom(580, 400)).toBe(true);
    expect(list.isAtBottom(100, 400)).toBe(false);
  });

  it("handles an empty list", () => {
    const list = new VirtualList();
    const view = list.windowFor(0, 500);
    expect(view).toEqual({ start: 0, end: 0, offsetTop: 0, totalHeight: 0 });
  });

  it("clamps a scroll position past the end", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(5);
    const view = list.windowFor(99999, 400);
    expect(view.start).toBeLessThanOrEqual(4);
    expect(view.end).toBe(5);
  });

  it("stays correct after many interleaved measurements", () => {
    // The prefix sums are rebuilt lazily from the first dirty index; this walks that
    // path in an order that would expose a stale-offset bug.
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(50);
    for (const index of [49, 0, 25, 10, 40, 3]) list.measure(index, 200);

    let expected = 0;
    for (let index = 0; index < 50; index++) {
      expect(list.offsetOf(index)).toBeCloseTo(expected, 5);
      expected += list.heightOf(index);
    }
    expect(list.totalHeight).toBeCloseTo(expected, 5);
  });

  it("finds the right item for every offset in a mixed-height list", () => {
    const list = new VirtualList({ estimatedItemHeight: 50, overscan: 0 });
    list.setCount(6);
    const heights = [30, 300, 45, 120, 60, 500];
    heights.forEach((height, index) => list.measure(index, height));

    let top = 0;
    heights.forEach((height, index) => {
      const view = list.windowFor(top + height / 2, 1);
      expect(view.start).toBe(index);
      top += height;
    });
  });
});
