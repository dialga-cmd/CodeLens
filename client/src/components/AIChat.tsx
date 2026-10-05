"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  FileCode,
  Loader2,
  SendHorizontal,
  User as UserIcon,
  Wrench,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { API_BASE_URL, type ChatSource } from "@/lib/api";
import { SSEParser, type SSEMessage } from "@/lib/sse";

type Role = "user" | "assistant";

interface Message {
  id: string;
  role: Role;
  content: string;
  sources?: ChatSource[];
  tools?: string[];
  pending?: boolean;
  failed?: boolean;
}

const SUGGESTIONS = [
  "Where is authentication handled?",
  "Which file should I read first to understand the entry point?",
  "What are the riskiest places to change?",
];

/**
 * Ask the analysed repository a question.
 *
 * Answers come back as a stream on the fast model, and the server sends one
 * `sources` event before the first token naming the files the answer is based
 * on - including any the model fetched with a tool mid-answer. Those links are
 * rendered under the message rather than mentioned in prose, because "the model
 * says" and "the model read" are different claims and the second one should be
 * checkable.
 */
export default function AIChat({
  repoId,
  getIdToken,
  disabled = false,
  className,
}: {
  repoId: string;
  getIdToken: () => Promise<string | null>;
  disabled?: boolean;
  className?: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [value, setValue] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState("");

  const streamRef = useRef<AbortController | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const sequence = useRef(0);

  useEffect(() => {
    const element = logRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [messages, streaming]);

  // Closing the tab mid-answer should close the request, not leak it.
  useEffect(() => () => streamRef.current?.abort(), []);

  const ask = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || streaming) return;

      const turn = ++sequence.current;
      setError("");
      setValue("");
      setStreaming(true);
      setMessages((current) => [
        ...current,
        { id: `u-${turn}`, role: "user", content: trimmed },
        { id: `a-${turn}`, role: "assistant", content: "", pending: true },
      ]);

      const controller = new AbortController();
      streamRef.current = controller;

      const patch = (id: string, changes: Partial<Message>) =>
        setMessages((current) =>
          current.map((message) => (message.id === id ? { ...message, ...changes } : message))
        );

      const answerId = `a-${turn}`;

      try {
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        const token = await getIdToken();
        if (token) headers.Authorization = `Bearer ${token}`;

        const response = await fetch(`${API_BASE_URL}/chat/stream`, {
          method: "POST",
          headers,
          body: JSON.stringify({ repo_id: repoId, query: trimmed }),
          signal: controller.signal,
        });

        if (!response.ok || !response.body) {
          let detail = `The assistant answered with ${response.status}.`;
          try {
            const payload = await response.json();
            if (typeof payload?.detail === "string") detail = payload.detail;
          } catch {
            // A non-JSON body is reported by status, which is enough.
          }
          throw new Error(detail);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        const sse = new SSEParser();

        // Server-Sent Events, read by a parser rather than by splitting on a
        // string: the server terminates them with CRLF, so splitting on "\n\n"
        // never matched a single event.
        const handle = (message: SSEMessage) => {
          let payload: any;
          try {
            payload = JSON.parse(message.data);
          } catch {
            return;
          }

          if (message.event === "sources") {
            patch(answerId, {
              sources: payload.sources || [],
              tools: (payload.tools_used || []).map((tool: any) =>
                typeof tool === "string" ? tool : String(tool?.name || "tool")
              ),
            });
          } else if (message.event === "delta") {
            setMessages((current) =>
              current.map((message_) =>
                message_.id === answerId
                  ? { ...message_, content: message_.content + String(payload.text ?? "") }
                  : message_
              )
            );
          } else if (message.event === "error") {
            throw new Error(String(payload.message || "The assistant failed."));
          } else if (message.event === "done" && payload.ok === false) {
            throw new Error("The assistant stopped before finishing the answer.");
          }
        };

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const message of sse.push(decoder.decode(value, { stream: true }))) {
            handle(message);
          }
        }
        // The stream can close straight after writing the last event, with no
        // closing blank line, and the decoder can still be holding the tail of a
        // multi-byte character. Both are flushed, so the last event is not lost.
        sse.push(decoder.decode());
        for (const message of sse.flush()) handle(message);

        setMessages((current) =>
          current.map((message) =>
            message.id === answerId
              ? {
                  ...message,
                  pending: false,
                  content:
                    message.content.trim() ||
                    "The model returned an empty answer. Try naming a file or a directory.",
                }
              : message
          )
        );
      } catch (cause) {
        if ((cause as Error)?.name === "AbortError") {
          setMessages((current) =>
            current.map((message) =>
              message.id === answerId
                ? { ...message, pending: false, content: message.content || "Stopped." }
                : message
            )
          );
        } else {
          const reason = cause instanceof Error ? cause.message : "The assistant is unavailable.";
          setError(reason);
          setMessages((current) =>
            current.map((message) =>
              message.id === answerId
                ? { ...message, pending: false, failed: true, content: message.content || reason }
                : message
            )
          );
        }
      } finally {
        streamRef.current = null;
        setStreaming(false);
      }
    },
    [getIdToken, repoId, streaming]
  );

  const busy = streaming;
  const canAsk = useMemo(() => Boolean(repoId) && !disabled, [repoId, disabled]);

  return (
    // Only the body lives here. The panel and its title belong to whoever mounts
    // this, so wrapping it in its own panel produced two stacked headers.
    <div className={`flex h-full min-h-0 flex-col ${className ?? ""}`}>
      <div
        ref={logRef}
        role="log"
        aria-live="polite"
        className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4 text-xs"
      >
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 py-6 text-center">
            <div className="grid h-14 w-14 place-items-center rounded-full border border-[#00ff41]/20 bg-[#00ff41]/10">
              <Bot size={26} className="text-[#00ff41]" aria-hidden />
            </div>
            <p className="max-w-sm text-[#7a7a7a]">
              {disabled
                ? "This is a stored analysis. The clone it was made from is not kept, so the assistant cannot read files from it - run the analysis yourself for the full assistant."
                : "Questions are answered from this analysis, and the model reads files through tools when the summary is not enough. Every file it used is listed under its answer."}
            </p>
            {!disabled && (
              <ul className="space-y-2">
                {SUGGESTIONS.map((suggestion) => (
                  <li key={suggestion}>
                    <button
                      type="button"
                      onClick={() => void ask(suggestion)}
                      disabled={busy}
                      className="rounded border border-white/10 px-3 py-1.5 text-[11px] text-[#6a6a6a] transition-colors hover:border-[#00ff41]/40 hover:text-[#00ff41] disabled:opacity-50"
                    >
                      {suggestion}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {messages.map((message) => (
          <article
            key={message.id}
            className={`flex gap-2.5 ${message.role === "user" ? "justify-end" : ""}`}
          >
            {message.role === "assistant" && (
              <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-white/10 bg-[#111] text-[#00ff41]">
                <Bot size={12} aria-hidden />
              </span>
            )}

            <div
              className={`min-w-0 max-w-[85%] rounded-lg border px-3 py-2 leading-relaxed ${
                message.role === "user"
                  ? "rounded-tr-sm border-[#00ff41]/20 bg-[#00ff41]/10 text-[#00ff41]"
                  : message.failed
                    ? "rounded-tl-sm border-red-500/30 bg-red-500/5 text-red-300"
                    : "rounded-tl-sm border-white/10 bg-[#111] text-[#d0d0d0]"
              }`}
            >
              {message.role === "assistant" ? (
                <>
                  <div className="prose prose-invert prose-xs max-w-none prose-p:my-1 prose-pre:overflow-x-auto prose-pre:rounded prose-pre:border prose-pre:border-white/10 prose-pre:bg-[#050505] prose-code:text-[#00ff41] prose-a:text-[#00ff41]">
                    {message.content ? (
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
                    ) : (
                      <span className="text-[#5a5a5a]">
                        {message.pending ? (
                          <span className="inline-flex items-center gap-2">
                            <Loader2 size={12} className="animate-spin" aria-hidden />
                            reading the repository
                          </span>
                        ) : (
                          "..."
                        )}
                      </span>
                    )}
                  </div>

                  {(message.tools?.length || message.sources?.length) && (
                    <div className="mt-2 border-t border-white/10 pt-2">
                      {!!message.tools?.length && (
                        <p className="flex items-center gap-1.5 text-[10px] text-[#5a5a5a]">
                          <Wrench size={10} aria-hidden />
                          {message.tools.join(", ")}
                        </p>
                      )}
                      {!!message.sources?.length && (
                        <ul className="mt-1 space-y-1">
                          {message.sources.slice(0, 8).map((source, index) => (
                            <li key={`${source.path}-${index}`}>
                              <button
                                type="button"
                                onClick={() => {
                                  setValue(`Tell me about ${source.path}`);
                                  textareaRef.current?.focus();
                                }}
                                className="flex max-w-full items-center gap-1.5 text-left font-mono text-[10px] text-[#6a6a6a] hover:text-[#00ff41]"
                              >
                                <FileCode size={10} className="shrink-0" aria-hidden />
                                <span className="truncate">{source.path}</span>
                                <span className="shrink-0 text-[#333]">{source.source}</span>
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </>
              ) : (
                <p className="whitespace-pre-wrap break-words">{message.content}</p>
              )}
            </div>

            {message.role === "user" && (
              <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full border border-white/10 bg-[#111] text-[#8a8a8a]">
                <UserIcon size={12} aria-hidden />
              </span>
            )}
          </article>
        ))}
      </div>

      {error && !disabled && (
        <p role="alert" className="shrink-0 border-t border-white/10 px-4 py-2 text-[11px] text-amber-300">
          {error}
        </p>
      )}

      <form
        className="shrink-0 border-t border-white/10 bg-[#050505] p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void ask(value);
        }}
      >
        <div className="flex items-end gap-2">
          <label htmlFor="codelens-chat-input" className="sr-only">
            Ask a question about this repository
          </label>
          <textarea
            id="codelens-chat-input"
            ref={textareaRef}
            rows={1}
            value={value}
            disabled={!canAsk || busy}
            placeholder={disabled ? "Stored analyses cannot be questioned" : "Ask about this codebase"}
            onChange={(event) => setValue(event.target.value)}
            onKeyDown={(event) => {
              // Enter sends; Shift+Enter is a newline, as in any chat window.
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                void ask(value);
              }
            }}
            className="max-h-32 min-h-[38px] flex-1 resize-none rounded border border-white/10 bg-transparent px-3 py-2 text-xs text-white placeholder:text-[#4d4d4d] focus:border-[#00ff41]/50 focus:outline-none disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={!canAsk || busy || !value.trim()}
            aria-label="Send question"
            className="grid h-9 w-9 shrink-0 place-items-center rounded bg-[#00ff41] text-black transition-transform hover:scale-105 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? (
              <Loader2 size={15} className="animate-spin" aria-hidden />
            ) : (
              <SendHorizontal size={15} aria-hidden />
            )}
          </button>
        </div>
      </form>
    </div>
  );
}