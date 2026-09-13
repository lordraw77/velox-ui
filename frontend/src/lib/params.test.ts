import { describe, expect, it } from "vitest";
import { PARAM_SPECS, fromForm, toForm } from "./params";

describe("parameter form conversion", () => {
  it("types values and leaves empty fields unset", () => {
    const { values, invalid } = fromForm(
      {
        temperature: "0.7",
        top_k: "40",
        num_ctx: "",
        mirostat: "2",
        penalize_nl: "false",
        keep_alive: "10m",
        stop: "<|end|>\n\n###\n",
      },
      PARAM_SPECS,
    );
    expect(invalid).toEqual([]);
    expect(values).toEqual({
      temperature: 0.7,
      top_k: 40,
      mirostat: 2,
      penalize_nl: false,
      keep_alive: "10m",
      stop: ["<|end|>", "###"],
    });
  });

  it("rejects out-of-range, fractional and malformed values without saving them", () => {
    const { values, invalid } = fromForm(
      { temperature: "-1", top_k: "4.5", mirostat: "3", keep_alive: "forever", top_p: "0.9" },
      PARAM_SPECS,
    );
    expect(invalid.sort()).toEqual(["keep_alive", "mirostat", "temperature", "top_k"]);
    expect(values).toEqual({ top_p: 0.9 });
  });

  it("accepts Ollama's special values for output length and GPU layers", () => {
    // -1 means no limit and -2 fill the context; num_gpu -1 lets Ollama decide.
    const { values, invalid } = fromForm({ max_tokens: "-1", num_gpu: "-1" }, PARAM_SPECS);
    expect(invalid).toEqual([]);
    expect(values).toEqual({ max_tokens: -1, num_gpu: -1 });
  });

  it("round-trips saved values", () => {
    const saved = { temperature: 0.2, stop: ["a", "b"], cache_prompt: true };
    expect(fromForm(toForm(saved), PARAM_SPECS).values).toEqual(saved);
  });

  it("defines each parameter once, with a help text", () => {
    const names = PARAM_SPECS.map((spec) => spec.name);
    expect(new Set(names).size).toBe(names.length);
    for (const spec of PARAM_SPECS) expect(spec.help).toBe(`params.${spec.name}`);
  });
});
