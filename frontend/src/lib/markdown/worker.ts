/**
 * The markdown worker.
 *
 * Parsing markdown and highlighting code is the most expensive thing this interface
 * does, and it happens while tokens are arriving. On the main thread that competes
 * with rendering for the same frame budget; on a machine that is also running the
 * model it competes with inference for the same cores. So it runs here (ADR-0012).
 *
 * Only the *stable* prefix of a message is parsed. The block still being written is
 * returned as plain text and appended by the caller, so nothing is re-parsed as the
 * parser changes its mind about what a half-written fence means.
 *
 * Syntax highlighting is loaded on first use and per language. A conversation with no
 * code never downloads a highlighter, and one with Python never downloads the other
 * hundred grammars.
 */

import { marked } from "marked";
import { splitStreamingText } from "./blocks";
import { escapeHtml, escapeRawHtml, isSafeUrl } from "./sanitize";
import type { RenderRequest, RenderResponse } from "./protocol";

type HighlightModule = typeof import("highlight.js/lib/core");

let highlighter: HighlightModule | null = null;
const loadedLanguages = new Set<string>();
const failedLanguages = new Set<string>();

/** Languages worth having: everything else falls back to no highlighting. */
const LANGUAGE_MODULES: Record<string, () => Promise<{ default: unknown }>> = {
  bash: () => import("highlight.js/lib/languages/bash"),
  c: () => import("highlight.js/lib/languages/c"),
  cpp: () => import("highlight.js/lib/languages/cpp"),
  csharp: () => import("highlight.js/lib/languages/csharp"),
  css: () => import("highlight.js/lib/languages/css"),
  diff: () => import("highlight.js/lib/languages/diff"),
  dockerfile: () => import("highlight.js/lib/languages/dockerfile"),
  go: () => import("highlight.js/lib/languages/go"),
  ini: () => import("highlight.js/lib/languages/ini"),
  java: () => import("highlight.js/lib/languages/java"),
  javascript: () => import("highlight.js/lib/languages/javascript"),
  json: () => import("highlight.js/lib/languages/json"),
  kotlin: () => import("highlight.js/lib/languages/kotlin"),
  lua: () => import("highlight.js/lib/languages/lua"),
  markdown: () => import("highlight.js/lib/languages/markdown"),
  php: () => import("highlight.js/lib/languages/php"),
  python: () => import("highlight.js/lib/languages/python"),
  ruby: () => import("highlight.js/lib/languages/ruby"),
  rust: () => import("highlight.js/lib/languages/rust"),
  sql: () => import("highlight.js/lib/languages/sql"),
  swift: () => import("highlight.js/lib/languages/swift"),
  typescript: () => import("highlight.js/lib/languages/typescript"),
  xml: () => import("highlight.js/lib/languages/xml"),
  yaml: () => import("highlight.js/lib/languages/yaml"),
};

/** Aliases people actually type in a fence. */
const ALIASES: Record<string, string> = {
  sh: "bash",
  shell: "bash",
  zsh: "bash",
  js: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  py: "python",
  rb: "ruby",
  rs: "rust",
  yml: "yaml",
  html: "xml",
  svelte: "xml",
  toml: "ini",
  cfg: "ini",
  "c++": "cpp",
  cs: "csharp",
  md: "markdown",
  postgres: "sql",
  psql: "sql",
};

function normaliseLanguage(name: string): string {
  const lowered = name.trim().toLowerCase();
  return ALIASES[lowered] ?? lowered;
}

async function ensureLanguage(language: string): Promise<boolean> {
  if (loadedLanguages.has(language)) return true;
  if (failedLanguages.has(language)) return false;

  const loader = LANGUAGE_MODULES[language];
  if (!loader) {
    failedLanguages.add(language);
    return false;
  }

  try {
    highlighter ??= (await import("highlight.js/lib/core")).default as unknown as HighlightModule;
    const grammar = await loader();
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (highlighter as any).registerLanguage(language, grammar.default);
    loadedLanguages.add(language);
    return true;
  } catch {
    // A highlighter that fails to load must not cost the reader their message.
    failedLanguages.add(language);
    return false;
  }
}

/**
 * Configure marked.
 *
 * `async: false` is deliberate. Highlighting is asynchronous because grammars are
 * lazily imported, so it cannot happen inside the parser; instead the parser emits a
 * placeholder and highlighting is applied to the finished HTML in a second pass. That
 * keeps the parse synchronous and the code path predictable.
 */
marked.use({
  gfm: true,
  breaks: false,
  async: false,
  renderer: {
    link({ href, title, tokens }) {
      const text = this.parser.parseInline(tokens);
      if (!isSafeUrl(href)) return text;
      const attributes = title ? ` title="${escapeHtml(title)}"` : "";
      // External links get noopener: a shared conversation is opened by strangers.
      return `<a href="${escapeHtml(href)}"${attributes} target="_blank" rel="noopener noreferrer">${text}</a>`;
    },
    image({ href, title, text }) {
      if (!isSafeUrl(href)) return escapeHtml(text);
      const attributes = title ? ` title="${escapeHtml(title)}"` : "";
      return `<img src="${escapeHtml(href)}" alt="${escapeHtml(text)}"${attributes} loading="lazy" />`;
    },
    code({ text, lang }) {
      const language = lang ? normaliseLanguage(lang) : "";
      const label = language ? ` data-language="${escapeHtml(language)}"` : "";
      // The body is escaped here and re-highlighted in the second pass, which
      // replaces it with markup derived from this same escaped text.
      return `<pre${label}><code class="hljs">${escapeHtml(text)}</code></pre>`;
    },
  },
});

const CODE_BLOCK = /<pre data-language="([^"]+)"><code class="hljs">([\s\S]*?)<\/code><\/pre>/g;

const DECODE: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
};

function decodeEntities(text: string): string {
  return text.replace(/&(?:amp|lt|gt|quot|#39);/g, (entity) => DECODE[entity] ?? entity);
}

/** Apply highlighting to the code blocks of already-rendered HTML. */
async function highlightCodeBlocks(html: string): Promise<string> {
  const matches = [...html.matchAll(CODE_BLOCK)];
  if (matches.length === 0) return html;

  const languages = new Set(matches.map((match) => match[1] ?? ""));
  const available = new Map<string, boolean>();
  for (const language of languages) {
    available.set(language, await ensureLanguage(language));
  }

  let result = "";
  let cursor = 0;
  for (const match of matches) {
    const index = match.index ?? 0;
    const language = match[1] ?? "";
    const body = match[2] ?? "";
    result += html.slice(cursor, index);

    if (available.get(language) && highlighter) {
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const { value } = (highlighter as any).highlight(decodeEntities(body), {
          language,
          ignoreIllegals: true,
        });
        result += `<pre data-language="${language}"><code class="hljs language-${language}">${value}</code></pre>`;
      } catch {
        result += match[0];
      }
    } else {
      result += match[0];
    }
    cursor = index + match[0].length;
  }
  result += html.slice(cursor);
  return result;
}

/** Render a markdown source to safe HTML. */
async function render(text: string, complete: boolean): Promise<{ html: string; tail: string }> {
  const { stable, tail } = splitStreamingText(text, complete);
  if (stable === "") return { html: "", tail };

  const parsed = marked.parse(escapeRawHtml(stable)) as string;
  return { html: await highlightCodeBlocks(parsed), tail };
}

self.onmessage = async (event: MessageEvent<RenderRequest>) => {
  const { id, text, complete, seq } = event.data;
  try {
    const { html, tail } = await render(text, complete);
    const response: RenderResponse = { id, seq, html, tail };
    self.postMessage(response);
  } catch {
    // Never leave the caller without a reply: a message that fails to render must
    // still show its text.
    const response: RenderResponse = { id, seq, html: "", tail: text };
    self.postMessage(response);
  }
};

export {};
