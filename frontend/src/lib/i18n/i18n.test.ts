import { describe, expect, it } from "vitest";
import en from "./en.json";
import italian from "./it.json";
import { detectLocale, translate } from "./index";

describe("translate", () => {
  it("returns the requested locale's string", () => {
    expect(translate("it", "chat.send")).toBe("Invia");
    expect(translate("en", "chat.send")).toBe("Send");
  });

  it("falls back to English for a missing key", () => {
    // A partially translated catalogue must produce a usable interface, not `app.name`.
    expect(translate("it", "app.name")).toBe("velox-ui");
  });

  it("returns the key when nothing has it", () => {
    expect(translate("en", "does.not.exist")).toBe("does.not.exist");
  });

  it("interpolates named placeholders", () => {
    expect(translate("en", "metrics.tokensPerSecond", { value: "41.2" })).toBe("41.2 tok/s");
    expect(translate("en", "chat.branch", { current: 2, total: 3 })).toBe("Branch 2 of 3");
  });

  it("leaves an unsupplied placeholder visible rather than printing undefined", () => {
    expect(translate("en", "chat.branch", { current: 2 })).toBe("Branch 2 of {total}");
  });
});

// The JSON catalogue is imported as `italian`, not `it`: `it` is vitest's own test
// function, and shadowing it makes every test in the file fail with a type error that
// says nothing about the cause.
describe("catalogues", () => {
  it("translates every key the Italian catalogue claims", () => {
    // Guard against a stale translation: a key removed from English but left in
    // Italian is dead weight that looks like coverage.
    const english = new Set(Object.keys(en));
    const orphans = Object.keys(italian).filter((key) => !english.has(key));
    expect(orphans).toEqual([]);
  });

  it("keeps placeholders consistent between locales", () => {
    // A translation that drops {value} silently shows a metric with no number.
    const placeholders = (text: string) => (text.match(/\{(\w+)\}/g) ?? []).sort();
    for (const [key, translated] of Object.entries(italian)) {
      const english = (en as Record<string, string>)[key];
      if (!english) continue;
      expect(placeholders(translated), `placeholders differ for ${key}`).toEqual(
        placeholders(english),
      );
    }
  });

  it("has English as the complete catalogue", () => {
    expect(Object.keys(en).length).toBeGreaterThan(Object.keys(italian).length - 1);
  });
});

describe("detectLocale", () => {
  it("picks a supported language from the browser list", () => {
    expect(detectLocale(["it-IT", "en-GB"])).toBe("it");
    expect(detectLocale(["en-US"])).toBe("en");
  });

  it("falls back to English for anything unsupported", () => {
    expect(detectLocale(["ja-JP", "ko-KR"])).toBe("en");
    expect(detectLocale([])).toBe("en");
  });
});
