/**
 * Internationalisation.
 *
 * English is the source language, not a translation of anything (ADR-0016). Every
 * other catalogue is a partial overlay: a missing key falls back to English rather
 * than showing a raw key, because a half-translated interface is usable and one
 * showing `chat.send` is not.
 *
 * The catalogues are small enough to ship in the bundle. A lazy fetch per locale would
 * add a round trip before the first paint to save a couple of kilobytes.
 */

import en from "./en.json";
import it from "./it.json";

export type Locale = "en" | "it";

export const LOCALES: { code: Locale; label: string }[] = [
  { code: "en", label: "English" },
  { code: "it", label: "Italiano" },
];

const CATALOGUES: Record<Locale, Record<string, string>> = { en, it };

/** Pick the best supported locale for a browser language list. */
export function detectLocale(languages: readonly string[] = navigator.languages ?? []): Locale {
  for (const language of languages) {
    const base = language.toLowerCase().split("-")[0];
    if (base && base in CATALOGUES) return base as Locale;
  }
  return "en";
}

/**
 * Look up a string and interpolate `{named}` placeholders.
 *
 * @param locale - Active locale.
 * @param key - Catalogue key.
 * @param values - Placeholder values.
 * @returns The translated string, the English string, or the key as a last resort.
 */
export function translate(
  locale: Locale,
  key: string,
  values?: Record<string, string | number>,
): string {
  const template = CATALOGUES[locale]?.[key] ?? CATALOGUES.en[key] ?? key;
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
    name in values ? String(values[name]) : whole,
  );
}
