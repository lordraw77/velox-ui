import { describe, expect, it } from "vitest";
import { VirtualList } from "./list";

describe("VirtualList.prepend", () => {
  it("reports the height added above so the visible item does not move", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(10);
    list.measure(5, 200); // an item the reader is looking at
    const before = list.offsetOf(5);

    const added = list.prepend(4);

    expect(list.count).toBe(14);
    // The same message is now at index 9; with the returned delta applied to
    // scrollTop it sits exactly where it was on screen.
    expect(list.offsetOf(9)).toBe(before + added);
    expect(list.heightOf(9)).toBe(200);
    expect(list.isMeasured(9)).toBe(true);
    expect(list.isMeasured(0)).toBe(false);
  });

  it("estimates prepended items from what has been measured", () => {
    const list = new VirtualList({ estimatedItemHeight: 100 });
    list.setCount(2);
    list.measure(0, 300);
    list.measure(1, 500);
    expect(list.prepend(3)).toBe(3 * 400);
  });

  it("keeps later measurements of the prepended items consistent", () => {
    const list = new VirtualList({ estimatedItemHeight: 100, overscan: 0 });
    list.setCount(5);
    list.prepend(5);
    const tail = list.offsetOf(9);
    // A prepended item measured taller shifts everything after it by the difference,
    // and `measure` reports that difference for the scroll correction.
    expect(list.measure(2, 160)).toBe(60);
    expect(list.offsetOf(9)).toBe(tail + 60);
  });

  it("ignores an empty prepend", () => {
    const list = new VirtualList();
    list.setCount(3);
    expect(list.prepend(0)).toBe(0);
    expect(list.count).toBe(3);
  });
});
