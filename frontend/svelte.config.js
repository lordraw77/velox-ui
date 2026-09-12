import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

export default {
  preprocess: vitePreprocess(),
  compilerOptions: {
    // Runes mode everywhere. Svelte 5's reactivity is what makes the streaming sink
    // possible without re-rendering the conversation (ADR-0012).
    runes: true,
  },
};
