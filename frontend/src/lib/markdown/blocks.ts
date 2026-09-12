/**
 * Splitting streaming text into complete and incomplete blocks.
 *
 * Markdown cannot be parsed incrementally in any meaningful way: a triple-backtick
 * fence opens a code block whose meaning is unknown until it closes, and a half-written
 * table is not a table. Re-parsing the whole message on every frame is also wrong --
 * that is exactly the cost this project exists to avoid.
 *
 * So the text is split at block boundaries. Everything before the last unterminated
 * block is stable: it will never change again, so it is parsed once and cached. The
 * trailing fragment is shown as plain text until it closes (ADR-0012). The visible
 * effect is that a code block stays unformatted for the second or two it takes to
 * finish and then snaps into place, which is far better than formatting that flickers
 * as the parser changes its mind.
 */

export interface SplitText {
  /** Text that will not change again, safe to parse and cache. */
  stable: string;
  /** The trailing block still being written. Rendered as plain text. */
  tail: string;
}

const FENCE = /^\s{0,3}(`{3,}|~{3,})/;

/**
 * Split streaming text into a stable prefix and an in-progress tail.
 *
 * @param text - Everything received so far.
 * @param complete - When true the stream has ended, so all of it is stable.
 */
export function splitStreamingText(text: string, complete = false): SplitText {
  if (complete) return { stable: text, tail: "" };
  if (text === "") return { stable: "", tail: "" };

  const lines = text.split("\n");

  // Walk the lines tracking fence state, remembering the last position at which the
  // document was at a block boundary: outside any fence, at a blank line.
  let insideFence = false;
  let fenceMarker = "";
  let safeLineCount = 0;

  for (let index = 0; index < lines.length; index++) {
    const line = lines[index] ?? "";
    const marker = FENCE.exec(line)?.[1];

    if (insideFence) {
      if (marker && marker[0] === fenceMarker[0] && marker.length >= fenceMarker.length) {
        insideFence = false;
        fenceMarker = "";
        // The fence just closed: everything through this line is settled.
        safeLineCount = index + 1;
      }
      continue;
    }

    if (marker) {
      insideFence = true;
      fenceMarker = marker;
      continue;
    }

    // A blank line outside a fence ends a paragraph.
    if (line.trim() === "") safeLineCount = index + 1;
  }

  // The final line is always in progress while streaming: more characters may arrive
  // and turn one asterisk into bold, or a lone pipe into a table row.
  if (!insideFence && safeLineCount >= lines.length) {
    safeLineCount = Math.max(0, lines.length - 1);
  }

  if (safeLineCount <= 0) return { stable: "", tail: text };

  const stable = lines.slice(0, safeLineCount).join("\n");
  const tail = lines.slice(safeLineCount).join("\n");
  return { stable: stable === "" ? "" : stable + "\n", tail };
}
