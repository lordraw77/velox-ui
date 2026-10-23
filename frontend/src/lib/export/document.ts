/**
 * A conversation as a document: Markdown to download, PDF to build from it.
 *
 * Pure functions, no DOM and no store, so both formats are tested in node. What a
 * reader of the exported file wants is the exchange itself — the system prompt, then
 * each turn in order — not the scaffolding around it: reasoning, tool traces and
 * token counts stay in the application.
 */

import type { Message } from "$lib/api/types";

export interface ExportLabels {
  user: string;
  assistant: string;
  systemPrompt: string;
  exportedAt: string;
}

export interface ExportChat {
  title: string;
  systemPrompt: string;
  messages: Message[];
  /** When the export was made, already formatted for the reader's locale. */
  exportedAt: string;
}

/** The turns worth exporting: what the person wrote and what the model answered. */
export function exportableMessages(messages: Message[]): Message[] {
  return messages.filter(
    (message) =>
      (message.role === "user" || message.role === "assistant") && message.content.trim() !== "",
  );
}

function speaker(message: Message, labels: ExportLabels): string {
  if (message.role === "user") return labels.user;
  return message.model_ref ? `${labels.assistant} (${message.model_ref})` : labels.assistant;
}

/** Render the conversation as a Markdown document. */
export function toMarkdown(chat: ExportChat, labels: ExportLabels): string {
  const parts = [`# ${chat.title}`, `_${labels.exportedAt}: ${chat.exportedAt}_`];
  if (chat.systemPrompt.trim()) {
    parts.push(`## ${labels.systemPrompt}`, quote(chat.systemPrompt.trim()));
  }
  for (const message of exportableMessages(chat.messages)) {
    parts.push(`## ${speaker(message, labels)}`, message.content.trim());
  }
  return `${parts.join("\n\n")}\n`;
}

function quote(text: string): string {
  return text
    .split("\n")
    .map((line) => (line ? `> ${line}` : ">"))
    .join("\n");
}

/** One assistant reply as a document of its own, with the question it answers. */
export interface AnswerSection {
  /** The reply's number among the conversation's replies, from 1. */
  index: number;
  markdown: string;
}

/**
 * Split the conversation into one Markdown document per assistant reply. The
 * question that prompted it is quoted on top, so each file reads on its own.
 */
export function toAnswerSections(chat: ExportChat, labels: ExportLabels): AnswerSection[] {
  const sections: AnswerSection[] = [];
  let question = "";
  for (const message of exportableMessages(chat.messages)) {
    if (message.role === "user") {
      question = message.content.trim();
      continue;
    }
    const parts = [`# ${chat.title}`];
    if (question) parts.push(`## ${labels.user}`, quote(question));
    parts.push(`## ${speaker(message, labels)}`, message.content.trim());
    sections.push({ index: sections.length + 1, markdown: `${parts.join("\n\n")}\n` });
  }
  return sections;
}

/** A filesystem-safe file name from a conversation title. */
export function fileName(title: string, extension: string): string {
  const base = title
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/[\s_-]+/g, "-")
    .slice(0, 80)
    .replace(/-+$/, "")
    .toLowerCase();
  return `${base || "conversation"}.${extension}`;
}
