import { describe, expect, it } from "vitest";
import {
  etaSeconds,
  formatBytes,
  formatDuration,
  formatRate,
  gpuShare,
  progressFraction,
} from "./format";

describe("formatBytes", () => {
  it("uses decimal units, as Ollama's own listing does", () => {
    expect(formatBytes(1_359_293_444)).toBe("1.4 GB");
    expect(formatBytes(274_302_450)).toBe("274 MB");
    expect(formatBytes(999)).toBe("999 B");
    expect(formatBytes(6_121_827_879)).toBe("6.1 GB");
  });

  it("renders nothing for an unknown size rather than a misleading zero", () => {
    expect(formatBytes(null)).toBe("");
    expect(formatBytes(undefined)).toBe("");
    expect(formatBytes(Number.NaN)).toBe("");
    expect(formatRate(null)).toBe("");
  });
});

describe("progress", () => {
  it("measures against the total and clamps", () => {
    expect(progressFraction(50, 200)).toBe(0.25);
    expect(progressFraction(300, 200)).toBe(1);
    expect(progressFraction(10, null)).toBeNull();
    expect(progressFraction(null, 100)).toBeNull();
  });

  it("estimates time left only from a real rate", () => {
    expect(etaSeconds(100, 1100, 50)).toBe(20);
    expect(etaSeconds(100, 1100, null)).toBeNull();
    expect(etaSeconds(100, 1100, 0)).toBeNull();
  });
});

describe("formatDuration", () => {
  it("picks a readable unit", () => {
    expect(formatDuration(45)).toBe("45 s");
    expect(formatDuration(200)).toBe("3 min");
    expect(formatDuration(3600)).toBe("1 h");
    expect(formatDuration(4400)).toBe("1 h 13 min");
    expect(formatDuration(null)).toBe("");
  });
});

describe("gpuShare", () => {
  it("distinguishes CPU-only from unknown", () => {
    expect(gpuShare(0, 48_203_038)).toBe(0);
    expect(gpuShare(null, 48_203_038)).toBeNull();
    expect(gpuShare(2_000, 4_000)).toBe(0.5);
  });
});
