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
  ToolCallEvent,
  ToolResultEvent,
  ToolTraceEntry,
  UsageEvent,
} from "$lib/api/types";
import { markdown } from "$lib/markdown/client";
import { announceReply, askToNotify } from "$lib/notify";
import { TokenSink } from "$lib/stream/sink";
import { app } from "./app.svelte";

/** A custom model chosen to start the next conversation, applied on its first turn. */
interface PendingCustomModel {
  id: string;
  systemPrompt: string | null;
  params: Record<string, ParamValue> | null;
  knowledgeIds: string[];
  toolServerIds: string[];
  webToolsEnabled: boolean;
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
  /** Tool calls made for this turn, in order, once they have a result (or a rejection). */
  toolTrace: ToolTraceEntry[];
  /** A tool call currently blocked on `POST /api/tools/approve`, if any. */
  pendingApproval: ToolCallEvent | null;
}

function toRendered(message: Message): RenderedMessage {
  return {
    ...message,
    live: message.content,
    html: "",
    tail: message.content,
    showReasoning: false,
    citations: [],
    toolTrace: message.meta?.tool_trace ?? [],
    pendingApproval: null,
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
  // Turns left running while the reader is elsewhere, watched only for their end.
  #watchers = new Map<string, AbortController>();
  #detaching = false;
  #sink: TokenSink | null = null;
  #lastMarkdownAt = 0;
  #pendingCustomModel: PendingCustomModel | null = null;

  get isEmpty(): boolean {
    return this.messages.length === 0;
  }

  /** Load a stored conversation, replacing whatever is on screen.
   *
   * Leaving one conversation does not stop its turn (ADR-0024): the reply is still
   * being written, and this only stops watching it closely. Arriving at a conversation
   * whose turn is still running picks it back up.
   */
  async open(chatId: string, branch?: string): Promise<void> {
    this.#detach();
    this.#watchers.get(chatId)?.abort();
    this.#watchers.delete(chatId);
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
      return;
    } finally {
      this.loading = false;
    }
    await this.#attach(chatId);
  }

  /** Start a fresh, unsaved conversation. */
  reset(): void {
    this.#detach();
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
      toolServerIds: model.tools,
      webToolsEnabled: model.plugins.includes("tools"),
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

    // Asked for here, where a reply is about to be produced, rather than on load: a
    // permission prompt before the person has done anything gets dismissed.
    void askToNotify();

    let response: Response;
    try {
      response = await api.stream(
        `/api/chats/${chatId}/completions`,
        {
          content,
          model_ref: modelRef,
          parent_id: parentId ?? null,
          system_prompt: startingModel?.systemPrompt ?? undefined,
          params: startingModel?.params ?? undefined,
          knowledge_ids: startingModel?.knowledgeIds ?? undefined,
          tool_server_ids: startingModel?.toolServerIds ?? undefined,
          web_tools: app.toolsEnabled || (startingModel?.webToolsEnabled ?? false),
        },
        this.#controller.signal,
      );
    } catch (error) {
      this.streaming = false;
      this.#controller = null;
      app.report(error);
      return;
    }

    await this.#follow(response, this.#controller.signal, chatId, optimisticUser);
  }

  /**
   * Read a turn's events into the conversation until it ends.
   *
   * Shared by a turn this client started and one it is picking back up, because the
   * event protocol is the same either way: it begins with the `start` frame carrying
   * both message ids, so a replay rebuilds the turn exactly as the original arrival
   * would have (docs/design/03-http-api.md).
   */
  async #follow(
    response: Response,
    signal: AbortSignal,
    chatId: string,
    optimisticUser?: RenderedMessage,
  ): Promise<void> {
    const sink = new TokenSink();
    this.#sink = sink;
    let assistant: RenderedMessage | null = null;
    const pendingCalls = new Map<string, ToolCallEvent>();

    try {
      for await (const event of readSse(response.body!, signal)) {
        switch (event.event) {
          case "start": {
            const start = JSON.parse(event.data) as StartEvent;
            // The server allocated the ids before writing any row (ADR-0005), so the
            // optimistic user message can adopt its real id immediately.
            if (optimisticUser) optimisticUser.id = start.user_message_id;
            // On a replay the message may already be on screen; the id says so.
            assistant =
              this.messages.find((entry) => entry.id === start.message_id) ??
              this.#appendLocal({
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

          case "tool_call": {
            const call = JSON.parse(event.data) as ToolCallEvent;
            pendingCalls.set(call.id, call);
            if (assistant) {
              assistant.pendingApproval = call.approval === "required" ? call : null;
            }
            break;
          }

          case "tool_result": {
            const result = JSON.parse(event.data) as ToolResultEvent;
            const call = pendingCalls.get(result.id);
            pendingCalls.delete(result.id);
            if (assistant) {
              if (assistant.pendingApproval?.id === result.id) assistant.pendingApproval = null;
              assistant.toolTrace = [
                ...assistant.toolTrace,
                {
                  id: result.id,
                  name: call?.name ?? "",
                  args: call?.args ?? {},
                  ok: result.ok,
                  content: result.content,
                },
              ];
            }
            break;
          }

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
      // Finished while the reader was elsewhere: say so, in the tab and, where the
      // browser allows it, on the desktop.
      if (document.hidden || this.id !== chatId) announceReply(this.title, chatId);
    } catch (error) {
      if ((error as Error).name === "AbortError") {
        sink.flushNow();
        if (assistant) {
          assistant.live = sink.text;
          assistant.content = sink.text;
          // Detaching leaves the turn running, so the reply is not finished and must
          // not be shown as stopped; only an asked-for stop ends it.
          if (!this.#detaching) assistant.status = "stopped";
          this.#render(assistant, true);
        }
      } else {
        app.report(error);
        if (assistant) assistant.status = "error";
      }
    } finally {
      if (this.#sink === sink) {
        this.streaming = false;
        this.phase = null;
        this.#controller = null;
        this.#sink = null;
      }
      sink.dispose();
    }
  }

  /**
   * Stop the turn in flight, as an instruction rather than a side effect.
   *
   * Closing the connection stops nothing now (ADR-0024), so the stop button says so to
   * the server. What the model produced up to here is kept.
   */
  stop(): void {
    const chatId = this.id;
    this.#controller?.abort();
    this.#controller = null;
    this.#sink?.flushNow();
    this.streaming = false;
    this.phase = null;
    if (chatId) {
      this.#watchers.get(chatId)?.abort();
      this.#watchers.delete(chatId);
      void api.stopTurn(chatId).catch(() => {
        // Nothing to stop, or it ended first: either way there is nothing to say.
      });
    }
  }

  /**
   * Stop reading the turn without stopping it, and keep an ear on it.
   *
   * What leaving a conversation does. The watcher reads nothing but the end of the
   * turn (`from=now`), so the reply arriving is still announced.
   */
  #detach(): void {
    const chatId = this.id;
    const wasStreaming = this.streaming;
    this.#detaching = true;
    try {
      this.#controller?.abort();
    } finally {
      this.#detaching = false;
    }
    this.#controller = null;
    this.#sink?.flushNow();
    this.streaming = false;
    this.phase = null;
    if (wasStreaming && chatId) this.#watch(chatId, this.title);
  }

  /** Watch a turn elsewhere, to announce it when it ends. */
  #watch(chatId: string, title: string): void {
    if (this.#watchers.has(chatId)) return;
    const controller = new AbortController();
    this.#watchers.set(chatId, controller);
    void (async () => {
      try {
        const response = await api.stream(
          `/api/chats/${chatId}/stream?from=now`,
          undefined,
          controller.signal,
        );
        for await (const event of readSse(response.body!, controller.signal)) {
          if (event.event === "done" || event.event === "error") break;
        }
        if (!controller.signal.aborted) announceReply(title, chatId);
      } catch {
        // Gone, finished before the watcher attached, or the page is going away.
        // Nothing here is worth an error banner.
      } finally {
        this.#watchers.delete(chatId);
      }
    })();
  }

  /** Pick up a turn already running in the conversation just opened. */
  async #attach(chatId: string): Promise<void> {
    this.#controller = new AbortController();
    let response: Response;
    try {
      response = await api.stream(
        `/api/chats/${chatId}/stream`,
        undefined,
        this.#controller.signal,
      );
    } catch {
      // The ordinary case: no turn is running in this conversation.
      this.#controller = null;
      return;
    }
    this.streamError = null;
    this.streaming = true;
    await this.#follow(response, this.#controller.signal, chatId);
  }

  /** Approve or reject a tool call the stream is blocked on. */
  async approveTool(messageId: string, callId: string, approved: boolean): Promise<void> {
    const message = this.messages.find((entry) => entry.id === messageId);
    try {
      await api.approveToolCall(callId, approved);
      // The stream itself clears `pendingApproval` once `tool_result` arrives; this
      // only stops the button from being clicked twice while that is in flight.
      if (message?.pendingApproval?.id === callId) {
        message.pendingApproval = { ...message.pendingApproval, approval: undefined };
      }
    } catch (error) {
      app.report(error);
    }
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
      meta: null,
      created_at: Date.now(),
      sibling_index: 0,
      sibling_count: 1,
      live: partial.content,
      html: "",
      tail: partial.content,
      showReasoning: false,
      citations: [],
      toolTrace: [],
      pendingApproval: null,
    };
    this.messages = [...this.messages, message];
    return this.messages.at(-1)!;
  }
}

export const conversation = new ConversationStore();
