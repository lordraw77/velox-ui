/**
 * Variable-height virtual list.
 *
 * Chat messages have no fixed height: a one-word reply and a forty-line code block
 * live in the same list. That rules out the easy virtualisation where offset is
 * `index * rowHeight`, so this keeps measured heights per item and a prefix-sum index
 * over them, rebuilt lazily.
 *
 * Two behaviours matter more than raw speed and are the reason this is a module with
 * tests rather than a few lines inside a component:
 *
 * **Bottom anchoring.** A conversation is read at its end. When content is appended,
 * or when an item above the viewport is measured and turns out taller than estimated,
 * the scroll position must be corrected so the text under the reader's eyes does not
 * move. Getting this wrong produces a view that jitters while a reply streams in.
 *
 * **Estimates converge.** Unmeasured items use an estimate that is refined from what
 * has actually been measured, so the scrollbar stops lying as the user scrolls.
 */

export interface VirtualWindow {
  /** First index to render. */
  start: number;
  /** One past the last index to render. */
  end: number;
  /** Pixel offset of `start` from the top of the scroll content. */
  offsetTop: number;
  /** Total height of all items. */
  totalHeight: number;
}

export interface VirtualListOptions {
  /** Height assumed for an item that has never been measured. */
  estimatedItemHeight?: number;
  /** Extra items rendered above and below the viewport. */
  overscan?: number;
}

const DEFAULT_ESTIMATE = 96;
const DEFAULT_OVERSCAN = 4;

export class VirtualList {
  #heights: number[] = [];
  #measured: boolean[] = [];
  /** Prefix sums: `#offsets[i]` is the top of item `i`. Length is count + 1. */
  #offsets: number[] = [0];
  #dirtyFrom = 0;
  #measuredCount = 0;
  #measuredTotal = 0;
  readonly #baseEstimate: number;
  readonly #overscan: number;

  constructor(options: VirtualListOptions = {}) {
    this.#baseEstimate = options.estimatedItemHeight ?? DEFAULT_ESTIMATE;
    this.#overscan = options.overscan ?? DEFAULT_OVERSCAN;
  }

  get count(): number {
    return this.#heights.length;
  }

  /**
   * The height used for items that have not been measured.
   *
   * Averaging what has been measured beats a fixed constant: in a conversation of
   * long code blocks a 96-pixel guess makes the scrollbar wrong by an order of
   * magnitude, and every correction is a visible jump.
   */
  get estimate(): number {
    return this.#measuredCount > 0 ? this.#measuredTotal / this.#measuredCount : this.#baseEstimate;
  }

  /** Replace the item count, keeping heights already measured for surviving items. */
  setCount(count: number): void {
    if (count === this.#heights.length) return;
    if (count < this.#heights.length) {
      for (let index = count; index < this.#heights.length; index++) {
        if (this.#measured[index]) {
          this.#measuredCount -= 1;
          this.#measuredTotal -= this.#heights[index] ?? 0;
        }
      }
      this.#heights.length = count;
      this.#measured.length = count;
    } else {
      while (this.#heights.length < count) {
        this.#heights.push(this.estimate);
        this.#measured.push(false);
      }
    }
    this.#markDirty(Math.min(count, this.#heights.length));
  }

  /**
   * Insert unmeasured items at the start, as when older messages are loaded.
   *
   * @returns The height added above every existing item, which the caller adds to
   *   `scrollTop` so the message the reader is looking at stays where it is. When the
   *   inserted items are measured later, {@link measure} reports the remaining
   *   correction the same way.
   */
  prepend(count: number): number {
    if (count <= 0) return 0;
    const estimate = this.estimate;
    this.#heights.splice(0, 0, ...new Array<number>(count).fill(estimate));
    this.#measured.splice(0, 0, ...new Array<boolean>(count).fill(false));
    this.#markDirty(0);
    return estimate * count;
  }

  /**
   * Forget every measured height and fall back to estimates.
   *
   * Heights are measured at whatever width the viewport had at the time. Change
   * that width — a rotation, the keyboard opening, a desktop window resize — and
   * every measurement is stale, but only the handful of items currently rendered
   * will re-measure themselves. The rest keep heights from the old width, so the
   * prefix sums (and with them the scroll position) drift.
   *
   * Dropping everything back to the running estimate is deliberately blunt: the
   * items on screen re-measure immediately on the next frame, and the ones off
   * screen were going to be wrong either way. Note this resets the estimate to its
   * base, since the average it was derived from was itself measured at the old
   * width.
   */
  invalidateMeasurements(): void {
    if (this.#measuredCount === 0) return;
    this.#measuredCount = 0;
    this.#measuredTotal = 0;
    this.#measured.fill(false);
    this.#heights.fill(this.#baseEstimate);
    this.#markDirty(0);
  }

  /**
   * Record a measured height.
   *
   * @returns The pixel delta this measurement introduced above the item, which the
   *   caller adds to `scrollTop` when the item sits above the viewport. Without that
   *   correction, measuring an off-screen item shifts everything the reader is
   *   currently looking at.
   */
  measure(index: number, height: number): number {
    if (index < 0 || index >= this.#heights.length) return 0;
    const previous = this.#heights[index] ?? this.estimate;
    if (this.#measured[index] && Math.abs(previous - height) < 0.5) return 0;

    if (this.#measured[index]) {
      this.#measuredTotal += height - previous;
    } else {
      this.#measured[index] = true;
      this.#measuredCount += 1;
      this.#measuredTotal += height;
    }
    this.#heights[index] = height;
    this.#markDirty(index);
    return height - previous;
  }

  /** Whether an item's height is a real measurement rather than an estimate. */
  isMeasured(index: number): boolean {
    return this.#measured[index] === true;
  }

  /** Height of one item, measured or estimated. */
  heightOf(index: number): number {
    return this.#heights[index] ?? this.estimate;
  }

  /** Pixel offset of an item's top edge. */
  offsetOf(index: number): number {
    this.#rebuild();
    return this.#offsets[Math.max(0, Math.min(index, this.#heights.length))] ?? 0;
  }

  /** Total height of the scroll content. */
  get totalHeight(): number {
    this.#rebuild();
    return this.#offsets[this.#heights.length] ?? 0;
  }

  /**
   * The window of items to render for a given scroll position.
   *
   * @param scrollTop - Current scroll offset.
   * @param viewportHeight - Height of the visible area.
   */
  windowFor(scrollTop: number, viewportHeight: number): VirtualWindow {
    this.#rebuild();
    const total = this.#offsets[this.#heights.length] ?? 0;
    if (this.#heights.length === 0) {
      return { start: 0, end: 0, offsetTop: 0, totalHeight: 0 };
    }

    const first = this.#indexAt(Math.max(0, scrollTop));
    // The last *visible* pixel, not the first pixel past the viewport. Using the
    // boundary itself pulls in the item that starts exactly where the viewport ends,
    // which has zero pixels on screen.
    const last = this.#indexAt(Math.max(0, scrollTop + viewportHeight - 1));

    const start = Math.max(0, first - this.#overscan);
    const end = Math.min(this.#heights.length, last + 1 + this.#overscan);
    return {
      start,
      end,
      offsetTop: this.#offsets[start] ?? 0,
      totalHeight: total,
    };
  }

  /** Scroll offset that puts the end of the list at the bottom of the viewport. */
  bottomScrollTop(viewportHeight: number): number {
    return Math.max(0, this.totalHeight - viewportHeight);
  }

  /**
   * Whether the viewport is close enough to the end to count as "at the bottom".
   *
   * A tolerance is needed rather than an equality check: a streaming reply grows the
   * content between frames, and fractional device pixels mean `scrollTop` rarely
   * lands exactly on the end.
   */
  isAtBottom(scrollTop: number, viewportHeight: number, tolerance = 48): boolean {
    return scrollTop + viewportHeight >= this.totalHeight - tolerance;
  }

  #markDirty(from: number): void {
    this.#dirtyFrom = Math.min(this.#dirtyFrom, Math.max(0, from));
  }

  /** Rebuild the prefix sums from the first dirty index onward. */
  #rebuild(): void {
    if (this.#dirtyFrom >= this.#heights.length && this.#offsets.length === this.#heights.length + 1) {
      return;
    }
    const from = Math.min(this.#dirtyFrom, this.#heights.length);
    this.#offsets.length = this.#heights.length + 1;
    let running = this.#offsets[from] ?? 0;
    for (let index = from; index < this.#heights.length; index++) {
      this.#offsets[index] = running;
      running += this.#heights[index] ?? this.estimate;
    }
    this.#offsets[this.#heights.length] = running;
    this.#dirtyFrom = this.#heights.length;
  }

  /** Binary search for the item containing a pixel offset. */
  #indexAt(offset: number): number {
    let low = 0;
    let high = this.#heights.length - 1;
    let found = high;
    while (low <= high) {
      const middle = (low + high) >> 1;
      const top = this.#offsets[middle] ?? 0;
      const bottom = top + (this.#heights[middle] ?? this.estimate);
      if (offset < top) {
        high = middle - 1;
      } else if (offset >= bottom) {
        low = middle + 1;
      } else {
        return middle;
      }
      found = Math.max(0, Math.min(low, this.#heights.length - 1));
    }
    return found;
  }
}
