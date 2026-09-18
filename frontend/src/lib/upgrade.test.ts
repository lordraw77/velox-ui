/**
 * The upgrade guard: it must reload once, and then stop.
 *
 * A page older than the server cannot fetch the panel code it was built with, and
 * reloading is the fix. What matters is the "once": a reload that does not fix it must
 * not become a loop, which is a far worse failure than a panel that will not open.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { alreadyReloaded, isStaleChunkError, reloadForUpgrade } from "./upgrade";

function fakeStorage(): Storage {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, value),
    removeItem: (key: string) => void values.delete(key),
    clear: () => values.clear(),
    key: () => null,
    length: 0,
  } as Storage;
}

describe("recognising a chunk this page can no longer fetch", () => {
  it.each([
    "Failed to fetch dynamically imported module: http://host/assets/ModelsPanel-abc.js",
    "error loading dynamically imported module",
    "Importing a module script failed.",
  ])("reads %s as a stale build", (message) => {
    expect(isStaleChunkError(new Error(message))).toBe(true);
    expect(isStaleChunkError(message)).toBe(true);
  });

  it("leaves ordinary failures alone", () => {
    expect(isStaleChunkError(new Error("NetworkError when attempting to fetch"))).toBe(false);
    expect(isStaleChunkError(undefined)).toBe(false);
  });
});

describe("reloading, once", () => {
  const reload = vi.fn();

  beforeEach(() => {
    reload.mockClear();
    vi.stubGlobal("sessionStorage", fakeStorage());
    vi.stubGlobal("location", { reload } as unknown as Location);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reloads the first time and remembers it", () => {
    expect(reloadForUpgrade(1_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    expect(alreadyReloaded(1_000)).toBe(true);
  });

  it("does not reload again straight away", () => {
    reloadForUpgrade(1_000);
    expect(reloadForUpgrade(5_000)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("is willing again once the window has passed", () => {
    reloadForUpgrade(1_000);
    expect(reloadForUpgrade(1_000 + 31_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it("still reloads when storage is unavailable", () => {
    vi.stubGlobal("sessionStorage", undefined);
    expect(reloadForUpgrade(1_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
