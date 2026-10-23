/**
 * Markdown to PDF, without the browser's print dialog.
 *
 * `marked` lexes the source into tokens and each token becomes a pdfmake content
 * node, so the file is a real text PDF (selectable, searchable, small), not a
 * screenshot of the page. `toPdfDefinition` is pure and tested in node; only
 * `downloadPdf` touches pdfmake, which is imported on demand — it and its fonts are
 * a large chunk that nobody should download until they ask for a PDF.
 */

import type { TDocumentDefinitions, TVirtualFileSystem } from "pdfmake/interfaces";
import { marked, type Token, type Tokens } from "marked";
import { fileName } from "./document";

// pdfmake's node types are loose; this is the part of the document model we emit.
type Node = Record<string, unknown>;
export type PdfDefinition = TDocumentDefinitions;

const MONO = "Courier";

/** Inline tokens to a pdfmake text run, carrying the style down through nesting. */
function inline(tokens: Token[] | undefined, style: Node = {}): Node[] {
  const runs: Node[] = [];
  for (const token of tokens ?? []) {
    switch (token.type) {
      case "strong":
        runs.push(...inline((token as Tokens.Strong).tokens, { ...style, bold: true }));
        break;
      case "em":
        runs.push(...inline((token as Tokens.Em).tokens, { ...style, italics: true }));
        break;
      case "del":
        runs.push(...inline((token as Tokens.Del).tokens, { ...style, decoration: "lineThrough" }));
        break;
      case "codespan":
        runs.push({ ...style, text: (token as Tokens.Codespan).text, font: MONO, background: "#eeeeee" });
        break;
      case "link": {
        const link = token as Tokens.Link;
        runs.push(
          ...inline(link.tokens, { ...style, link: link.href, color: "#1a5fb4", decoration: "underline" }),
        );
        break;
      }
      case "image":
        runs.push({ ...style, text: (token as Tokens.Image).text || "[image]", italics: true });
        break;
      case "br":
        runs.push({ ...style, text: "\n" });
        break;
      case "text": {
        const text = token as Tokens.Text;
        if (text.tokens?.length) runs.push(...inline(text.tokens, style));
        else runs.push({ ...style, text: text.text });
        break;
      }
      case "html":
      case "escape":
      default:
        // Raw HTML has no PDF meaning; keep any text, drop the tags.
        runs.push({ ...style, text: stripTags((token as { text?: string }).text ?? token.raw) });
    }
  }
  return runs;
}

function stripTags(text: string): string {
  return text.replace(/<[^>]*>/g, "");
}

const HEADING_SIZE = [0, 20, 17, 14.5, 13, 12, 11];

function block(token: Token): Node[] {
  switch (token.type) {
    case "heading": {
      const heading = token as Tokens.Heading;
      return [
        {
          text: inline(heading.tokens),
          bold: true,
          fontSize: HEADING_SIZE[heading.depth] ?? 11,
          margin: [0, heading.depth <= 2 ? 10 : 6, 0, 4],
        },
      ];
    }
    case "paragraph":
      return [{ text: inline((token as Tokens.Paragraph).tokens), margin: [0, 0, 0, 6] }];
    case "text": {
      // Top-level or tight-list text that was not wrapped in a paragraph.
      const text = token as Tokens.Text;
      return [{ text: inline(text.tokens ?? [text]), margin: [0, 0, 0, 2] }];
    }
    case "code": {
      const code = token as Tokens.Code;
      return [
        {
          table: {
            widths: ["*"],
            body: [[{ text: code.text, font: MONO, fontSize: 9, preserveLeadingSpaces: true }]],
          },
          layout: {
            fillColor: () => "#f3f3f3",
            hLineColor: () => "#dddddd",
            vLineColor: () => "#dddddd",
          },
          margin: [0, 2, 0, 8],
        },
      ];
    }
    case "blockquote":
      return [
        {
          table: { widths: ["*"], body: [[{ stack: blocks((token as Tokens.Blockquote).tokens) }]] },
          layout: {
            hLineWidth: () => 0,
            vLineWidth: (index: number) => (index === 0 ? 2.5 : 0),
            vLineColor: () => "#bbbbbb",
            paddingLeft: () => 8,
          },
          color: "#555555",
          margin: [0, 2, 0, 8],
        },
      ];
    case "list": {
      const list = token as Tokens.List;
      const items = list.items.map((item) => ({ stack: blocks(item.tokens) }));
      return [
        list.ordered
          ? { ol: items, start: Number(list.start) || 1, margin: [0, 0, 0, 6] }
          : { ul: items, margin: [0, 0, 0, 6] },
      ];
    }
    case "table": {
      const table = token as Tokens.Table;
      const head = table.header.map((cell) => ({ text: inline(cell.tokens), bold: true, fillColor: "#eeeeee" }));
      const rows = table.rows.map((row) =>
        row.map((cell, index) => ({ text: inline(cell.tokens), alignment: table.align[index] ?? "left" })),
      );
      return [
        {
          table: { headerRows: 1, widths: table.header.map(() => "*"), body: [head, ...rows] },
          layout: { hLineColor: () => "#cccccc", vLineColor: () => "#cccccc" },
          fontSize: 9.5,
          margin: [0, 2, 0, 8],
        },
      ];
    }
    case "hr":
      return [
        {
          canvas: [{ type: "line", x1: 0, y1: 0, x2: 515, y2: 0, lineWidth: 0.7, lineColor: "#cccccc" }],
          margin: [0, 6, 0, 8],
        },
      ];
    case "html":
      return stripTags((token as Tokens.HTML).text).trim() ? [{ text: stripTags((token as Tokens.HTML).text) }] : [];
    case "space":
      return [];
    default:
      return [{ text: token.raw }];
  }
}

function blocks(tokens: Token[]): Node[] {
  return tokens.flatMap(block);
}

/** The pdfmake document for one piece of Markdown. */
export function toPdfDefinition(source: string, title: string): PdfDefinition {
  return {
    info: { title },
    pageSize: "A4",
    pageMargins: [40, 48, 40, 48],
    defaultStyle: { fontSize: 10.5, lineHeight: 1.25 },
    content: blocks(marked.lexer(source, { gfm: true })) as unknown as TDocumentDefinitions["content"],
    footer: (page: number, pages: number) => ({
      text: `${page} / ${pages}`,
      alignment: "center",
      fontSize: 8,
      color: "#888888",
    }),
  };
}

/** pdfmake with its fonts, loaded once and only when a PDF is first asked for. */
async function loadPdfMake() {
  const [{ default: pdfMake }, vfs] = await Promise.all([
    import("pdfmake/build/pdfmake"),
    import("pdfmake/build/vfs_fonts"),
  ]);
  pdfMake.addVirtualFileSystem(((vfs as { default?: unknown }).default ?? vfs) as TVirtualFileSystem);
  return pdfMake;
}

/** Build the PDF for one piece of Markdown. */
export async function buildPdf(source: string, title: string): Promise<Blob> {
  const pdfMake = await loadPdfMake();
  return pdfMake.createPdf(toPdfDefinition(source, title)).getBlob();
}

/** Build several PDFs one after the other, so only one is being laid out at a time. */
export async function buildPdfs(
  documents: { source: string; title: string }[],
): Promise<Blob[]> {
  const pdfMake = await loadPdfMake();
  const blobs: Blob[] = [];
  for (const { source, title } of documents) {
    blobs.push(await pdfMake.createPdf(toPdfDefinition(source, title)).getBlob());
  }
  return blobs;
}

/** Build the PDF and hand it to the browser as a download. */
export async function downloadPdf(source: string, title: string): Promise<void> {
  const pdfMake = await loadPdfMake();
  await pdfMake.createPdf(toPdfDefinition(source, title)).download(fileName(title, "pdf"));
}
