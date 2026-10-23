/**
 * Export the conversation on screen as Markdown or PDF.
 *
 * Loaded on demand from the header, so none of this
 * counts against the initial bundle.
 *
 * PDF is converted from the Markdown source with pdfmake (see `pdf.ts`), loaded on
 * first use: the whole conversation as one file, or one file per assistant reply
 * bundled into a ZIP so the browser is not asked to allow dozens of downloads.
 */

import { api } from "$lib/api/client";
import type { Message } from "$lib/api/types";
import { formatDate } from "$lib/format";
import { app } from "$lib/stores/app.svelte";
import { conversation } from "$lib/stores/conversation.svelte";
import { fileName, toAnswerSections, toMarkdown, type ExportChat, type ExportLabels } from "./document";

export type ExportFormat = "md" | "pdf" | "pdf-sections";

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
  } else if (format === "pdf") {
    // Converted from the Markdown source, not printed: a real PDF file, no dialog.
    const { downloadPdf } = await import("./pdf");
    await downloadPdf(toMarkdown(chat, labels), chat.title);
  } else {
    await downloadSections(chat, labels);
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

/** One PDF per assistant reply, delivered together as a ZIP. */
async function downloadSections(chat: ExportChat, labels: ExportLabels): Promise<void> {
  const sections = toAnswerSections(chat, labels);
  if (sections.length === 0) return;
  const [{ buildPdfs }, { zip }] = await Promise.all([import("./pdf"), import("./zip")]);
  const blobs = await buildPdfs(sections.map((section) => ({ source: section.markdown, title: chat.title })));
  const width = String(sections.length).length;
  const entries = await Promise.all(
    sections.map(async (section, position) => ({
      name: fileName(`${String(section.index).padStart(width, "0")} ${chat.title}`, "pdf"),
      data: new Uint8Array(await blobs[position]!.arrayBuffer()),
    })),
  );
  const archive = zip(entries);
  download(archive, fileName(chat.title, "zip"));
}

function download(content: string | Blob, name: string, type = "application/octet-stream"): void {
  const url = URL.createObjectURL(typeof content === "string" ? new Blob([content], { type }) : content);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
