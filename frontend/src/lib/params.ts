/**
 * The advanced parameter panel's field definitions and form conversion.
 *
 * The panel shows only the parameters the model's backend accepts — the server lists
 * them as `supported_params` — so a field here is shown for Ollama and hidden for a
 * cloud API without any check on the backend's name.
 *
 * Parameter names are shown as they are, not translated: `num_ctx` is what the
 * documentation, the forums and the Modelfile call it. The help text is translated.
 *
 * An empty field means "not set": the backend, or the model's own Modelfile, decides.
 */

import type { ParamValue } from "./api/types";

export type ParamKind = "float" | "int" | "bool" | "text" | "list" | "choice";

export type ParamGroup = "sampling" | "penalties" | "mirostat" | "runtime";

export interface ParamSpec {
  name: string;
  /** Catalogue key of the help text. */
  help: string;
  kind: ParamKind;
  group: ParamGroup;
  min?: number;
  max?: number;
  step?: number;
  choices?: readonly number[];
}

export const PARAM_GROUPS: readonly { key: ParamGroup; label: string }[] = [
  { key: "sampling", label: "params.groupSampling" },
  { key: "penalties", label: "params.groupPenalties" },
  { key: "mirostat", label: "params.groupMirostat" },
  { key: "runtime", label: "params.groupRuntime" },
];

export const PARAM_SPECS: readonly ParamSpec[] = [
  { name: "temperature", help: "params.temperature", kind: "float", group: "sampling", min: 0, max: 5, step: 0.05 },
  { name: "top_p", help: "params.top_p", kind: "float", group: "sampling", min: 0, max: 1, step: 0.01 },
  { name: "top_k", help: "params.top_k", kind: "int", group: "sampling", min: 0, max: 100000, step: 1 },
  { name: "min_p", help: "params.min_p", kind: "float", group: "sampling", min: 0, max: 1, step: 0.01 },
  { name: "typical_p", help: "params.typical_p", kind: "float", group: "sampling", min: 0, max: 1, step: 0.01 },
  { name: "tfs_z", help: "params.tfs_z", kind: "float", group: "sampling", min: 0, max: 100, step: 0.05 },
  { name: "seed", help: "params.seed", kind: "int", group: "sampling", step: 1 },
  { name: "max_tokens", help: "params.max_tokens", kind: "int", group: "sampling", min: -2, max: 10000000, step: 1 },
  { name: "stop", help: "params.stop", kind: "list", group: "sampling" },
  { name: "repeat_penalty", help: "params.repeat_penalty", kind: "float", group: "penalties", min: 0, max: 10, step: 0.05 },
  { name: "presence_penalty", help: "params.presence_penalty", kind: "float", group: "penalties", min: -2, max: 2, step: 0.1 },
  { name: "frequency_penalty", help: "params.frequency_penalty", kind: "float", group: "penalties", min: -2, max: 2, step: 0.1 },
  { name: "penalize_nl", help: "params.penalize_nl", kind: "bool", group: "penalties" },
  { name: "mirostat", help: "params.mirostat", kind: "choice", group: "mirostat", choices: [0, 1, 2] },
  { name: "mirostat_tau", help: "params.mirostat_tau", kind: "float", group: "mirostat", min: 0, max: 100, step: 0.1 },
  { name: "mirostat_eta", help: "params.mirostat_eta", kind: "float", group: "mirostat", min: 0, max: 10, step: 0.01 },
  { name: "num_ctx", help: "params.num_ctx", kind: "int", group: "runtime", min: 64, max: 100000000, step: 256 },
  { name: "num_gpu", help: "params.num_gpu", kind: "int", group: "runtime", min: -1, max: 100000, step: 1 },
  { name: "num_thread", help: "params.num_thread", kind: "int", group: "runtime", min: 1, max: 4096, step: 1 },
  { name: "num_batch", help: "params.num_batch", kind: "int", group: "runtime", min: 1, max: 1000000, step: 1 },
  { name: "keep_alive", help: "params.keep_alive", kind: "text", group: "runtime" },
  { name: "cache_prompt", help: "params.cache_prompt", kind: "bool", group: "runtime" },
];

/** Form state: every field is the string the input holds; empty means unset. */
export type ParamForm = Record<string, string>;

const KEEP_ALIVE = /^-?\d+(\.\d+)?(ms|s|m|h)?$/;
const MAX_STOP_SEQUENCES = 16;

/** Saved values to form strings. */
export function toForm(values: Record<string, ParamValue>): ParamForm {
  const form: ParamForm = {};
  for (const [name, value] of Object.entries(values)) {
    form[name] = Array.isArray(value) ? value.join("\n") : String(value);
  }
  return form;
}

/**
 * Form strings to typed values, validated against each field's range.
 *
 * @returns The values to save, and the names of fields that are not valid. Nothing
 *   invalid is ever included in `values`, so a partial save cannot slip through.
 */
export function fromForm(
  form: ParamForm,
  specs: readonly ParamSpec[],
): { values: Record<string, ParamValue>; invalid: string[] } {
  const values: Record<string, ParamValue> = {};
  const invalid: string[] = [];

  for (const spec of specs) {
    const raw = (form[spec.name] ?? "").trim();
    if (raw === "") continue;

    switch (spec.kind) {
      case "float":
      case "int":
      case "choice": {
        const number = Number(raw);
        const inRange =
          Number.isFinite(number) &&
          (spec.min === undefined || number >= spec.min) &&
          (spec.max === undefined || number <= spec.max);
        const integral = spec.kind === "float" || Number.isInteger(number);
        const allowed = spec.kind !== "choice" || (spec.choices ?? []).includes(number);
        if (inRange && integral && allowed) values[spec.name] = number;
        else invalid.push(spec.name);
        break;
      }
      case "bool":
        if (raw === "true" || raw === "false") values[spec.name] = raw === "true";
        else invalid.push(spec.name);
        break;
      case "text":
        if (spec.name !== "keep_alive" || KEEP_ALIVE.test(raw)) values[spec.name] = raw;
        else invalid.push(spec.name);
        break;
      case "list": {
        const items = raw
          .split("\n")
          .map((item) => item.trim())
          .filter((item) => item !== "");
        if (items.length <= MAX_STOP_SEQUENCES) values[spec.name] = items;
        else invalid.push(spec.name);
        break;
      }
    }
  }
  return { values, invalid };
}
