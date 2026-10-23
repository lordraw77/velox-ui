import { describe, expect, it } from "vitest";
import { toPdfDefinition } from "./pdf";

describe("toPdfDefinition", () => {
  it("turns markdown blocks into pdfmake content", () => {
    const md = "# Title\n\nHello **bold** and `code`.\n\n- a\n- b\n\n1. x\n\n```py\nprint(1)\n```\n\n| h |\n|---|\n| c |\n\n> quote\n\n---\n";
    const definition = toPdfDefinition(md, "T");
    const content = definition.content as unknown as Record<string, unknown>[];
    expect(content[0]).toMatchObject({ bold: true, fontSize: 20 });
    expect(JSON.stringify(content[1])).toContain('"bold":true');
    expect(content.some((node) => "ul" in node)).toBe(true);
    expect(content.some((node) => "ol" in node)).toBe(true);
    expect(content.some((node) => "table" in node)).toBe(true);
    expect(JSON.stringify(content)).toContain("print(1)");
  });

  it("drops raw html tags but keeps their text", () => {
    const content = toPdfDefinition("<b>hi</b>", "T").content as unknown as Record<string, unknown>[];
    expect(JSON.stringify(content)).not.toContain("<b>");
    expect(JSON.stringify(content)).toContain("hi");
  });
});
