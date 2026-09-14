/**
 * The conversation being viewed, and the machinery that streams into it.
 *
 * The streaming design is the whole point of this module. A reply arrives as hundreds
 * of small events; each one must reach the screen quickly without re-rendering the
 * conversation. So:
 *
 * - Tokens go into a {@link TokenSink}, which publishes once per animation frame.
 * - The frame publish updates a single reactive field on the streaming message, so
 *   Svelte re-renders that message's text and nothing else.
 * - Markdown is re-rendered in the worker, throttled harder than the text itself:
 *   formatting a paragraph twenty times a second buys nothing a reader can perceive,
 *   and on a machine also running the model it is CPU taken from inference.
 *
 * `error` and `done` arrive as events inside a 200 response, because by then the
 * response has already started. That is why the stream carries a typed error rather
 * than relying on the status code (docs/design/03-http-api.md).
 */

import { api } from "$lib/api/client";
import { readSse } from "$lib/api/sse";
import type {
  ApiError,
  Chat,
  CitationEvent,
  CustomModel,
  Message,
  ParamValue,
  StartEvent,
  StreamPhase,
  UsageEvent,
} from "$lib/api/types";
import { markdown } from "$lib/markdown/client";
import { TokenSink } from "$lib/stream/sink";
import { app } from "./app.svelte";

/** A custom model chosen to start the next conversation, applied on its first turn. */
interface PendingCustomModel {
  id: string;
  systemPrompt: string | null;
  params: Record<string, ParamValue> | null;
  knowledgeIds: string[];
}

/** How often a streaming message's markdown is re-rendered, in milliseconds. */
const MARKDOWN_INTERVAL_MS = 180;

/** A message plus the view state that only exists while it is on screen. */
export interface RenderedMessage extends Message {
  /** Text as published by the sink; diverges from `content` only while streaming. */
  live: string;
  /** Rendered HTML of the stable prefix. */
  html: string;
  /** Unformatted trailing block. */
  tail: string;
  /** Whether the reasoning block is expanded. */
  showReasoning: boolean;
  /** Sources retrieved for this turn, when a knowledge collection was attached. */
  citations: CitationEvent[];
}

function toRendered(message: Message): RenderedMessage {
  return {
    ...message,
    live: message.content,
    html: "",
    tail: message.content,
    showReasoning: false,
    citations: [],
  };
}

class ConversationStore {
  id = $state<string | null>(null);
  title = $state("");
  messages = $state<RenderedMessage[]>([]);
  loading = $state(false);

  /** Set while a turn is in flight. */
  streaming = $state(false);
  phase = $state<StreamPhase | null>(null);
  usage = $state<UsageEvent | null>(null);
  streamError = $state<ApiError | null>(null);

  /** Cursor for the page before the oldest loaded message; null once at the start. */
  olderCursor = $state<string | null>(null);
  loadingOlder = $state(false);

  #controller: AbortController | null = null;
  #sink: TokenSink | null = null;
  #lastMarkdownAt = 0;
  #pendingCustomModel: PendingCustomModel | null = null;

  get isEmpty(): boolean {
    return this.messages.length === 0;
  }

  /** Load a stored conversation, replacing whatever is on screen. */
  async open(chatId: string, branch?: string): Promise<void> {
    this.stop();
    this.loading = true;
    try {
      const chat: Chat = await api.chat(chatId, branch);
      this.id = chat.id;
      this.title = chat.title;
      this.messages = chat.messages.map(toRendered);
      this.olderCursor = chat.messages_cursor;
      this.usage = null;
      this.streamError = null;
      this.#renderAll();
    } catch (error) {
      app.report(error);
    } finally {
      this.loading = false;
    }
  }

  /** Start a fresh, unsaved conversation. */
  reset(): void {
    this.stop();
    this.id = null;
    this.title = "";
    this.messages = [];
    this.olderCursor = null;
    this.usage = null;
    this.streamError = null;
    this.#pendingCustomModel = null;
  }

  /**
   * Start a fresh conversation from a custom model: its system prompt and parameter
   * overrides are applied to the first turn, the moment the chat is actually created.
   */
  startFromCustomModel(model: CustomModel): void {
    this.reset();
    this.#pendingCustomModel = {
      id: model.id,
      systemPrompt: model.system_prompt,
      params: model.params,
      knowledgeIds: model.knowledge_ids,
    };
  }

  /**
   * Load the page before the oldest message on screen.
   *
   * A conversation opens with its newest page only (ADR-0007), so its length never
   * decides how long opening it takes. Earlier pages arrive as the reader scrolls up.
   */
  async loadOlder(): Promise<void> {
    const chatId = this.id;
    const cursor = this.olderCursor;
    if (!chatId || !cursor || this.loadingOlder) return;
    this.loadingOlder = true;
    try {
      const page = await api.messages(chatId, cursor);
      if (this.id !== chatId) return;
      const older = page.items.map(toRendered);
      this.messages = [...older, ...this.messages];
      this.olderCursor = page.next_cursor;
      for (const message of older) {
        if (message.role === "assistant" && message.content) this.#render(message, true);
      }
    } catch (error) {
      app.report(error);
    } finally {
      this.loadingOlder = false;
    }
  }

  /** Render the markdown of every loaded message. */
  #renderAll(): void {
    for (const message of this.messages) {
      if (message.role === "assistant" && message.content) {
        this.#render(message, true);
      }
    }
  }

  #render(message: RenderedMessage, complete: boolean): void {
    markdown.request(message.id, message.live, complete, (html, tail) => {
      const current = this.messages.find((entry) => entry.id === message.id);
      if (!current) return;
      current.html = html;
      current.tail = tail;
    });
  }

  /**
   * Send a message and stream the reply.
   *
   * @param content - The user's text.
   * @param parentId - Branch point, for regenerating from an earlier turn.
   */
  async send(content: string, parentId?: string | null): Promise<void> {
    const modelRef = app.modelRef;
    if (!modelRef || this.streaming) return;

    let chatId = this.id;
    // Carried only into the turn that creates the chat: after that, the normal
    // per-model saved parameters (ParamsPanel) take over, same as any other chat.
    const startingModel = chatId === null ? this.#pendingCustomModel : null;
    if (chatId === null) {
      // A conversation is created on first send, not when the composer is focused, so
      // an abandoned draft leaves nothing behind.
      const created = await api.createChat(
        content.slice(0, 80) || app.t("chat.untitled"),
        modelRef,
        startingModel?.id,
      );
      chatId = created.id;
      this.id = created.id;
      this.title = created.title;
      this.#pendingCustomModel = null;
      await app.loadChats(true);
    }

    this.streamError = null;
    this.usage = null;
    this.phase = null;
    this.streaming = true;
    this.#controller = new AbortController();

    const optimisticUser = this.#appendLocal({
      id: `pending-${Date.now()}`,
      role: "user",
      content,
      parent_id: parentId ?? null,
    });

    const sink = new TokenSink();
    this.#sink = sink;
    let assistant: RenderedMessage | null = null;

    try {
      const response = await api.stream(
        `/api/chats/${chatId}/completions`,
        {
          content,
          model_ref: modelRef,
          parent_id: parentId ?? null,
          system_prompt: startingModel?.systemPrompt ?? undefined,
          params: startingModel?.params ?? undefined,
          knowledge_ids: startingModel?.knowledgeIds ?? undefined,
        },
        this.#controller.signal,
      );

      for await (const event of readSse(response.body!, this.#controller.signal)) {
        switch (event.event) {
          case "start": {
            const start = JSON.parse(event.data) as StartEvent;
            // The server allocated the ids before writing any row (ADR-0005), so the
            // optimistic user message can adopt its real id immediately.
            optimisticUser.id = start.user_message_id;
            assistant = this.#appendLocal({
              id: start.message_id,
              role: "assistant",
              content: "",
              parent_id: start.user_message_id,
              status: "streaming",
              model_ref: start.model_ref,
            });
            sink.subscribe((text) => {
              if (!assistant) return;
              assistant.live = text;
              this.#maybeRenderMarkdown(assistant);
            });
            break;
          }

          case "status":
            this.phase = (JSON.parse(event.data) as { phase: StreamPhase }).phase;
            break;

          case "delta":
            sink.push((JSON.parse(event.data) as { t: string }).t);
            break;

          case "reasoning":
            if (assistant) {
              assistant.reasoning = (assistant.reasoning ?? "") +
                (JSON.parse(event.data) as { t: string }).t;
            }
            break;

          case "citation":
            if (assistant) {
              assistant.citations = [...assistant.citations, JSON.parse(event.data) as CitationEvent];
            }
            break;

          case "usage":
            this.usage = JSON.parse(event.data) as UsageEvent;
            break;

          case "error":
            this.streamError = JSON.parse(event.data) as ApiError;
            if (assistant) assistant.status = "error";
            break;

          case "done":
            break;

          default:
            break;
        }
      }

      sink.flushNow();
      if (assistant) {
        assistant.live = sink.text;
        assistant.content = sink.text;
        if (assistant.status === "streaming") assistant.status = "complete";
        if (this.usage) {
          assistant.tokens_in = this.usage.tokens_in;
          assistant.tokens_out = this.usage.tokens_out;
          assistant.cost_micros = this.usage.cost_micros;
          assistant.timings = {
            ttft_ms: this.usage.ttft_ms,
            duration_ms: this.usage.duration_ms,
            tok_per_s: this.usage.tok_per_s,
            prompt_eval_ms: this.usage.prompt_eval_ms,
            eval_ms: this.usage.eval_ms,
          };
        }
        // One final render, unthrottled, with the stream marked complete so the last
        // block is formatted rather than left as plain text.
        this.#render(assistant, true);
      }
      if (this.id) app.touchChat(this.id, this.title);
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        // The user pressed Stop. The server keeps what the model produced.
        sink.flushNow();
        if (assistant) {
          assistant.live = sink.text;
          assistant.content = sink.text;
          assistant.status = "stopped";
          this.#render(assistant, true);
        }
      } else {
        app.report(error);
        if (assistant) assistant.status = "error";
      }
    } finally {
      this.streaming = false;
      this.phase = null;
      this.#controller = null;
      sink.dispose();
      this.#sink = null;
    }
  }

  /** Abort the turn in flight. */
  stop(): void {
    this.#controller?.abort();
    this.#controller = null;
    this.#sink?.flushNow();
  }

  /** Re-render markdown at most every {@link MARKDOWN_INTERVAL_MS}. */
  #maybeRenderMarkdown(message: RenderedMessage): void {
    const now = performance.now();
    if (now - this.#lastMarkdownAt < MARKDOWN_INTERVAL_MS) return;
    this.#lastMarkdownAt = now;
    this.#render(message, false);
  }

  #appendLocal(partial: Partial<Message> & Pick<Message, "id" | "role" | "content">): RenderedMessage {
    const previous = this.messages.at(-1);
    const message: RenderedMessage = {
      id: partial.id,
      parent_id: partial.parent_id ?? previous?.id ?? null,
      role: partial.role,
      content: partial.content,
      reasoning: null,
      status: partial.status ?? "complete",
      model_ref: partial.model_ref ?? null,
      depth: (previous?.depth ?? -1) + 1,
      tokens_in: null,
      tokens_out: null,
      cost_micros: null,
      timings: null,
      error: null,
      created_at: Date.now(),
      sibling_index: 0,
      sibling_count: 1,
      live: partial.content,
      html: "",
      tail: partial.content,
      showReasoning: false,
      citations: [],
    };
    this.messages = [...this.messages, message];
    return this.messages.at(-1)!;
  }
}

export const conversation = new ConversationStore();
