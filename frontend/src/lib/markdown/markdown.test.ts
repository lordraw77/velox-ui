import { describe, expect, it } from "vitest";
import { splitStreamingText } from "./blocks";
import { escapeHtml, escapeRawHtml, isSafeUrl } from "./sanitize";

const NL = "\n";
const TAB = "\t";
const ZWSP = "\u200b";
const TICK = "\u0060";
const FENCE = TICK + TICK + TICK;


describe("escapeHtml", () => {
  it("neutralises tag and attribute characters", () => {
    expect(escapeHtml(`<img src=x onerror="alert(1)">`)).toBe(
      "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;",
    );
  });

  it("escapes ampersands so entities cannot be smuggled", () => {
    expect(escapeHtml("&lt;script&gt;")).toBe("&amp;lt;script&amp;gt;");
  });
});

describe("escapeRawHtml", () => {
  it("escapes raw HTML outside code", () => {
    expect(escapeRawHtml("hello <script>alert(1)</script>")).toBe(
      "hello &lt;script&gt;alert(1)&lt;/script&gt;",
    );
  });

  it("leaves markdown syntax untouched", () => {
    const source = "# Title" + NL + NL + "**bold** and *italic* and [link](https://example.com)";
    expect(escapeRawHtml(source)).toBe(source);
  });

  it("leaves fenced code alone so it is not double-escaped", () => {
    // The parser escapes code contents itself. Escaping here as well would show the
    // reader a literal entity inside their own code block.
    const source = FENCE + "html" + NL + "<div>hi</div>" + NL + FENCE;
    expect(escapeRawHtml(source)).toBe(source);
  });

  it("leaves inline code alone", () => {
    const source = "use " + TICK + "<br>" + TICK + " for a break";
    expect(escapeRawHtml(source)).toBe(source);
  });

  it("still escapes HTML that follows a code block", () => {
    const source = FENCE + NL + "code" + NL + FENCE + NL + "then <img onerror=x>";
    const result = escapeRawHtml(source);
    expect(result).toContain("code");
    expect(result).toContain("&lt;img onerror=x&gt;");
  });

  it("survives an unterminated fence", () => {
    // Streaming text routinely ends in the middle of a fence.
    const result = escapeRawHtml("<b>before</b>" + NL + FENCE + "js" + NL + "let x = 1");
    expect(result).toContain("&lt;b&gt;before");
  });
});

describe("isSafeUrl", () => {
  it("allows ordinary web and mail links", () => {
    expect(isSafeUrl("https://example.com/a?b=c")).toBe(true);
    expect(isSafeUrl("http://example.com")).toBe(true);
    expect(isSafeUrl("mailto:someone@example.com")).toBe(true);
  });

  it("allows relative links", () => {
    expect(isSafeUrl("/api/docs")).toBe(true);
    expect(isSafeUrl("./chat")).toBe(true);
    expect(isSafeUrl("#section")).toBe(true);
  });

  it("refuses script and data URLs", () => {
    expect(isSafeUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeUrl("JavaScript:alert(1)")).toBe(false);
    expect(isSafeUrl("data:text/html,<script>alert(1)</script>")).toBe(false);
    expect(isSafeUrl("vbscript:msgbox(1)")).toBe(false);
  });

  it("refuses characters used to split a scheme", () => {
    expect(isSafeUrl("java" + NL + "script:alert(1)")).toBe(false);
    expect(isSafeUrl("java" + TAB + "script:alert(1)")).toBe(false);
    expect(isSafeUrl("java" + ZWSP + "script:alert(1)")).toBe(false);
  });

  it("refuses protocol-relative URLs", () => {
    expect(isSafeUrl("//evil.example.com")).toBe(false);
  });

  it("refuses nothing at all", () => {
    expect(isSafeUrl("")).toBe(false);
    expect(isSafeUrl("   ")).toBe(false);
  });
});

describe("splitStreamingText", () => {
  it("treats a completed stream as entirely stable", () => {
    const text = "one" + NL + "two";
    expect(splitStreamingText(text, true)).toEqual({ stable: text, tail: "" });
  });

  it("keeps the last line in the tail while streaming", () => {
    // More characters may arrive and change what the line means.
    const result = splitStreamingText("done paragraph" + NL + NL + "half a sen");
    expect(result.stable).toBe("done paragraph" + NL + NL);
    expect(result.tail).toBe("half a sen");
  });

  it("holds an unterminated fence entirely in the tail", () => {
    // A fence whose meaning is not yet known must not be handed to the parser: it
    // would render as a paragraph now and as code a second later.
    const text = "intro" + NL + NL + FENCE + "python" + NL + "x = 1";
    const result = splitStreamingText(text);
    expect(result.stable).toBe("intro" + NL + NL);
    expect(result.tail).toBe(FENCE + "python" + NL + "x = 1");
  });

  it("releases a fence once it closes", () => {
    const text = FENCE + "py" + NL + "x = 1" + NL + FENCE + NL + "after";
    const result = splitStreamingText(text);
    expect(result.stable).toContain("x = 1");
    expect(result.stable).toContain(FENCE);
    expect(result.tail).toBe("after");
  });

  it("does not close a fence on a shorter marker", () => {
    const text = FENCE + TICK + NL + "x" + NL + FENCE + NL + "still code";
    const result = splitStreamingText(text);
    expect(result.tail).toContain("still code");
    expect(result.stable).toBe("");
  });

  it("handles empty input", () => {
    expect(splitStreamingText("")).toEqual({ stable: "", tail: "" });
  });

  it("puts a single unfinished line entirely in the tail", () => {
    expect(splitStreamingText("Hel")).toEqual({ stable: "", tail: "Hel" });
  });

  it("never loses or duplicates a character", () => {
    // The property that matters: whatever the split, concatenating the two halves
    // must reproduce the input exactly. A streaming renderer that drops a character
    // is worse than one that formats late.
    const samples = [
      "a",
      "a" + NL + "b",
      "para" + NL + NL + FENCE + "js" + NL + "code",
      FENCE + NL + "x" + NL + FENCE + NL + NL + "tail",
      "# h" + NL + NL + "- one" + NL + "- two",
      NL + NL + NL,
    ];
    for (const sample of samples) {
      const { stable, tail } = splitStreamingText(sample);
      expect(stable + tail).toBe(sample);
    }
  });
});
