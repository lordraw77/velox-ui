import { describe, expect, it } from "vitest";
import type { Message } from "$lib/api/types";
import { fileName, toMarkdown, toAnswerSections, type ExportChat, type ExportLabels } from "./document";

const LABELS: ExportLabels = {
  user: "User",
  assistant: "Assistant",
  systemPrompt: "System prompt",
  exportedAt: "Exported",
};

function message(id: string, role: Message["role"], content: string, modelRef: string | null = null): Message {
  return {
    id,
    parent_id: null,
    role,
    content,
    reasoning: null,
    status: "complete",
    model_ref: modelRef,
    depth: 0,
    tokens_in: null,
    tokens_out: null,
    cost_micros: null,
    timings: null,
    error: null,
    meta: null,
    created_at: 0,
    sibling_index: 0,
    sibling_count: 1,
  };
}

const CHAT: ExportChat = {
  title: "Tea <time>",
  systemPrompt: "Be brief.\nAlways.",
  exportedAt: "today",
  messages: [
    message("u1", "user", "Is <b>green</b> tea hot?"),
    message("a1", "assistant", "**Yes**, usually.", "ollama:qwen"),
    message("t1", "tool", "{\"ignored\":true}"),
    message("a2", "assistant", "   "),
  ],
};

describe("toMarkdown", () => {
  it("writes the system prompt and each turn, skipping tools and empty replies", () => {
    expect(toMarkdown(CHAT, LABELS)).toBe(
      [
        "# Tea <time>",
        "_Exported: today_",
        "## System prompt",
        "> Be brief.\n> Always.",
        "## User",
        "Is <b>green</b> tea hot?",
        "## Assistant (ollama:qwen)",
        "**Yes**, usually.",
      ].join("\n\n") + "\n",
    );
  });

  it("omits the system prompt section when there is none", () => {
    expect(toMarkdown({ ...CHAT, systemPrompt: " " }, LABELS)).not.toContain("System prompt");
  });
});

describe("toAnswerSections", () => {
  it("makes one document per assistant reply, quoting its question", () => {
    const sections = toAnswerSections(CHAT, LABELS);
    expect(sections.length).toBe(CHAT.messages.filter((m) => m.role === "assistant" && m.content.trim()).length);
    expect(sections[0]?.index).toBe(1);
    expect(sections[0]?.markdown).toContain("## User\n\n> ");
  });
});

describe("fileName", () => {
  it("makes a safe, readable name", () => {
    expect(fileName("Perché il tè è verde? / Notes", "md")).toBe("perche-il-te-e-verde-notes.md");
  });

  it("falls back when nothing usable is left", () => {
    expect(fileName("???", "pdf")).toBe("conversation.pdf");
  });
});
