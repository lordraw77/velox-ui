import { describe, expect, it } from "vitest";
import { crc32, zip } from "./zip";

describe("zip", () => {
  it("computes the standard CRC-32", () => {
    expect(crc32(new TextEncoder().encode("123456789"))).toBe(0xcbf43926);
  });

  it("writes local headers, a central directory and an end record", async () => {
    const blob = zip([
      { name: "a.txt", data: new TextEncoder().encode("hello") },
      { name: "b.txt", data: new TextEncoder().encode("world") },
    ]);
    const bytes = new Uint8Array(await blob.arrayBuffer());
    const view = new DataView(bytes.buffer);
    expect(view.getUint32(0, true)).toBe(0x04034b50);
    const end = bytes.length - 22;
    expect(view.getUint32(end, true)).toBe(0x06054b50);
    expect(view.getUint16(end + 10, true)).toBe(2);
    expect(view.getUint32(view.getUint32(end + 16, true), true)).toBe(0x02014b50);
  });
});
