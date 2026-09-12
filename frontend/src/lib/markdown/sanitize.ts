/**
 * Markdown safety.
 *
 * Rendering model output as HTML is an injection vector, and not a theoretical one: a
 * model can be persuaded to emit an `onerror` attribute, a shared conversation is
 * served to other people, and a RAG answer can quote a document an attacker wrote.
 *
 * The approach here is to remove the capability rather than to filter it. Raw HTML is
 * escaped *before* the markdown parser ever sees it, so the parser's output contains
 * only tags the parser itself generated. That is a much smaller thing to get right
 * than a sanitiser allow-list, and it needs no DOM -- which matters, because this runs
 * in a worker where DOMPurify would have no document to work with.
 *
 * The one hole markdown leaves open afterwards is link targets: a `javascript:` URL
 * produces an anchor the parser generated with a destination the model chose. So URLs
 * are checked against a scheme allow-list.
 */

const ENTITIES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

const SAFE_SCHEMES = new Set(["http:", "https:", "mailto:", "tel:"]);

/**
 * Control and separator characters used to smuggle a scheme past a naive check, by
 * splitting it across a line break, a tab or a zero-width character.
 */
const CONTROL_CHARACTERS = /[\u0000-\u0020\u007f-\u009f\u200b-\u200d\ufeff]/;

/** Escape every character that could start an HTML tag or break out of an attribute. */
export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => ENTITIES[char] ?? char);
}

/**
 * Escape raw HTML in markdown source while leaving markdown syntax intact.
 *
 * Code spans and fenced blocks are left alone: the parser escapes their contents
 * itself, and escaping them here as well would double-encode, showing the reader a
 * literal entity inside their own code block.
 */
export function escapeRawHtml(markdown: string): string {
  const segments: string[] = [];
  let cursor = 0;
  // Fenced blocks first, then inline code spans. Anything outside them is escaped.
  const pattern = /(^|\n)(\s{0,3})(`{3,}|~{3,})[\s\S]*?(\n\s{0,3}\3|$)|(`+)[\s\S]*?\5/g;

  for (const match of markdown.matchAll(pattern)) {
    const index = match.index ?? 0;
    segments.push(escapeHtml(markdown.slice(cursor, index)));
    segments.push(match[0]);
    cursor = index + match[0].length;
  }
  segments.push(escapeHtml(markdown.slice(cursor)));
  return segments.join("");
}

/**
 * Decide whether a link or image URL may be rendered.
 *
 * Relative URLs are allowed; anything carrying a scheme must be on the allow-list. An
 * unparseable URL is refused rather than passed through: "the parser could not
 * understand it" is not evidence that a browser will not.
 */
export function isSafeUrl(url: string): boolean {
  const trimmed = url.trim();
  if (trimmed === "") return false;
  if (CONTROL_CHARACTERS.test(trimmed)) return false;
  if (/^[a-z][a-z0-9+.-]*:/i.test(trimmed)) {
    try {
      return SAFE_SCHEMES.has(new URL(trimmed).protocol);
    } catch {
      return false;
    }
  }
  return !trimmed.startsWith("//");
}
