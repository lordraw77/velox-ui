/**
 * A conversation as a document: Markdown to download, HTML to print.
 *
 * Pure functions, no DOM and no store, so both formats are tested in node. What a
 * reader of the exported file wants is the exchange itself — the system prompt, then
 * each turn in order — not the scaffolding around it: reasoning, tool traces and
 * token counts stay in the application.
 */

import type { Message } from "$lib/api/types";
import { escapeHtml } from "$lib/markdown/sanitize";

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

/**
 * Render the conversation as a standalone HTML page for printing.
 *
 * @param rendered - Safe HTML of each assistant message, by id, from the markdown
 *   worker. User messages are shown as written, as they are on screen.
 * @param stylesheet - URL of the print stylesheet. The page inherits the
 *   application's CSP, which forbids inline `<style>`, so it must be a file.
 */
export function toPrintHtml(
  chat: ExportChat,
  labels: ExportLabels,
  rendered: Map<string, string>,
  stylesheet: string,
): string {
  const turns = exportableMessages(chat.messages).map((message) => {
    const html = rendered.get(message.id);
    const body =
      message.role === "assistant" && html
        ? `<div class="md">${html}</div>`
        : `<p class="plain">${escapeHtml(message.content.trim())}</p>`;
    return `<section class="turn ${message.role}"><h2>${escapeHtml(speaker(message, labels))}</h2>${body}</section>`;
  });

  const system = chat.systemPrompt.trim()
    ? `<section class="turn system"><h2>${escapeHtml(labels.systemPrompt)}</h2><p class="plain">${escapeHtml(chat.systemPrompt.trim())}</p></section>`
    : "";

  return [
    "<!doctype html>",
    '<html><head><meta charset="utf-8">',
    `<title>${escapeHtml(chat.title)}</title>`,
    `<link rel="stylesheet" href="${escapeHtml(stylesheet)}">`,
    "</head><body>",
    `<header><h1>${escapeHtml(chat.title)}</h1>`,
    `<p class="meta">${escapeHtml(labels.exportedAt)}: ${escapeHtml(chat.exportedAt)}</p></header>`,
    system,
    ...turns,
    "</body></html>",
  ].join("");
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
