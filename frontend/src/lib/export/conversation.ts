/**
 * Export the conversation on screen as Markdown or PDF.
 *
 * Loaded on demand from the header, so none of this — nor the print stylesheet —
 * counts against the initial bundle.
 *
 * PDF goes through the browser's own print dialog ("Save as PDF") rather than a PDF
 * library: every browser already has a good one, and shipping a second costs
 * hundreds of kilobytes for a feature used now and then.
 */

import { api } from "$lib/api/client";
import type { Message } from "$lib/api/types";
import { formatDate } from "$lib/format";
import { markdown } from "$lib/markdown/client";
import { app } from "$lib/stores/app.svelte";
import { conversation } from "$lib/stores/conversation.svelte";
import { fileName, toMarkdown, toPrintHtml, type ExportChat, type ExportLabels } from "./document";
import stylesheet from "./print.css?url";

export type ExportFormat = "md" | "pdf";

export async function exportConversation(format: ExportFormat): Promise<void> {
  const chat = await collect();
  if (!chat) return;
  const labels: ExportLabels = {
    user: app.t("export.user"),
    assistant: app.t("export.assistant"),
    systemPrompt: app.t("systemPrompt.title"),
    exportedAt: app.t("export.exportedAt"),
  };
  if (format === "md") {
    download(toMarkdown(chat, labels), fileName(chat.title, "md"), "text/markdown;charset=utf-8");
  } else {
    const rendered = await renderAssistantMessages(chat.messages);
    print(toPrintHtml(chat, labels, rendered, new URL(stylesheet, location.href).href));
  }
}

/**
 * The whole active branch, not just the pages loaded so far: a conversation opens
 * with its newest page only (ADR-0007), and an export that silently starts halfway
 * through would be worse than none.
 */
async function collect(): Promise<ExportChat | null> {
  const chatId = conversation.id;
  if (!chatId) return null;
  let messages: Message[] = conversation.messages.map((message) => ({
    ...message,
    content: message.live || message.content,
  }));
  let cursor = conversation.olderCursor;
  while (cursor) {
    const page = await api.messages(chatId, cursor);
    messages = [...page.items, ...messages];
    cursor = page.next_cursor;
  }
  return {
    title: conversation.title || app.t("chat.untitled"),
    systemPrompt: conversation.systemPrompt,
    messages,
    exportedAt: formatDate(Date.now(), app.locale),
  };
}

/** Render assistant messages through the same worker the conversation uses. */
async function renderAssistantMessages(messages: Message[]): Promise<Map<string, string>> {
  const assistant = messages.filter((message) => message.role === "assistant" && message.content);
  const entries = await Promise.all(
    assistant.map(
      (message) =>
        new Promise<[string, string]>((resolve) => {
          const key = `export:${message.id}`;
          markdown.request(key, message.content, true, (html) => {
            markdown.release(key);
            // A lazy image in a hidden frame may never load, and would print blank.
            resolve([message.id, html.replaceAll(' loading="lazy"', "")]);
          });
        }),
    ),
  );
  return new Map(entries);
}

function download(text: string, name: string, type: string): void {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

let frame: HTMLIFrameElement | null = null;

/** Print an HTML document from a hidden frame, leaving the application untouched. */
function print(html: string): void {
  frame?.remove();
  const current = document.createElement("iframe");
  frame = current;
  current.setAttribute("aria-hidden", "true");
  current.tabIndex = -1;
  Object.assign(current.style, {
    position: "fixed",
    width: "0",
    height: "0",
    border: "0",
    visibility: "hidden",
  });
  current.addEventListener(
    "load",
    () => {
      const view = current.contentWindow;
      if (!view) return;
      view.addEventListener("afterprint", () => {
        current.remove();
        if (frame === current) frame = null;
      });
      view.focus();
      view.print();
    },
    { once: true },
  );
  current.srcdoc = html;
  document.body.append(current);
}
