/**
 * Formatting for model management: sizes, rates, durations and memory placement.
 *
 * Sizes use decimal units (1 GB = 10⁹ bytes), the same convention as Ollama's own
 * listing, so a model shows the same size here as in `ollama list`.
 */

const UNITS = ["B", "KB", "MB", "GB", "TB"] as const;

/** `1_359_293_444` → `"1.4 GB"`. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "";
  let value = bytes;
  let unit = 0;
  while (value >= 1000 && unit < UNITS.length - 1) {
    value /= 1000;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : 1;
  return `${value.toFixed(digits)} ${UNITS[unit]}`;
}

/** Transfer rate, e.g. `"12.5 MB/s"`. */
export function formatRate(bytesPerSecond: number | null | undefined): string {
  const size = formatBytes(bytesPerSecond);
  return size ? `${size}/s` : "";
}

/** Completed share in [0, 1], or `null` when there is no total to measure against. */
export function progressFraction(
  completed: number | null | undefined,
  total: number | null | undefined,
): number | null {
  if (!total || total <= 0 || completed === null || completed === undefined) return null;
  return Math.min(1, Math.max(0, completed / total));
}

/** Seconds left at the current rate, or `null` when it cannot be estimated honestly. */
export function etaSeconds(
  completed: number | null | undefined,
  total: number | null | undefined,
  rate: number | null | undefined,
): number | null {
  if (!total || completed === null || completed === undefined || !rate || rate <= 0) return null;
  return Math.max(0, (total - completed) / rate);
}

/** `45` → `"45 s"`, `200` → `"3 min"`, `4400` → `"1 h 13 min"`. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "";
  const whole = Math.max(0, Math.round(seconds));
  if (whole < 60) return `${whole} s`;
  const minutes = Math.round(whole / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/**
 * Share of a loaded model held in GPU memory, in [0, 1].
 *
 * `0` means it runs entirely on the CPU, which is the single most useful thing to know
 * about a slow reply. `null` means the backend did not say.
 */
export function gpuShare(
  vramBytes: number | null | undefined,
  sizeBytes: number | null | undefined,
): number | null {
  if (vramBytes === null || vramBytes === undefined || !sizeBytes) return null;
  return Math.min(1, Math.max(0, vramBytes / sizeBytes));
}

/** A local date and time for a timestamp in epoch milliseconds. */
export function formatDate(epochMs: number | null | undefined, locale: string): string {
  if (!epochMs) return "";
  return new Date(epochMs).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}
