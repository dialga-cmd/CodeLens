"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { AlertTriangle, FileCode, X } from "lucide-react";

import { api, type RepoFile } from "@/lib/api";

/** Past this the dialog stops rendering lines and reports the size instead. */
const MAX_PREVIEW_BYTES = 120_000;
/** Lines shown before the middle is dropped, so one long file cannot lock the tab. */
const MAX_PREVIEW_LINES = 4_000;

/**
 * One file from the analysed clone, in a dialog.
 *
 * The contents come from the server rather than from the analysis snapshot,
 * because the snapshot holds parsed summaries and the file on disk holds the
 * actual source - which is the thing somebody opened this to read.
 *
 * Every failure is stated rather than swallowed: an empty dialog looks exactly
 * like a file with nothing in it, and "this file was deleted" and "the server
 * said no" call for different reactions.
 */
export default function FileDialog({
  repoId,
  file,
  getIdToken,
  onClose,
}: {
  repoId: string;
  file: { path: string; language: string };
  getIdToken: () => Promise<string | null>;
  onClose: () => void;
}) {
  const [state, setState] = useState<
    { kind: "loading" } | { kind: "ready"; data: RepoFile } | { kind: "error"; message: string }
  >({ kind: "loading" });

  const headingId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  // Held so an in-flight fetch cannot set state after the dialog has gone.
  const cancelled = useRef(false);

  useEffect(() => {
    cancelled.current = false;
    setState({ kind: "loading" });

    api
      .repoFile(repoId, file.path, getIdToken)
      .then((data) => {
        if (cancelled.current) return;
        if (!data || typeof data.content !== "string") {
          setState({ kind: "error", message: "The server returned no content for this file." });
          return;
        }
        setState({ kind: "ready", data });
      })
      .catch((error: unknown) => {
        if (cancelled.current) return;
        const message =
          error instanceof Error && error.message ? error.message : "The file could not be loaded.";
        setState({ kind: "error", message });
      });

    return () => {
      cancelled.current = true;
    };
  }, [repoId, file.path, getIdToken]);

  // Escape closes, and the focus starts on the close button so a keyboard reader
  // is not left behind on the canvas with no way out.
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const { content, truncatedAt } = useMemoPreview(state);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-labelledby={headingId}
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-white/10 bg-[#0a0a0a] shadow-2xl"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex shrink-0 items-center gap-2 border-b border-white/10 bg-[#111] px-4 py-3">
          <FileCode size={14} className="text-[#00ff41]" aria-hidden />
          <h2 id={headingId} className="min-w-0 truncate font-mono text-xs text-[#d0d0d0]">
            {file.path}
          </h2>
          <span className="ml-auto flex shrink-0 items-center gap-3">
            {state.kind === "ready" && (
              <span className="text-[10px] uppercase tracking-widest text-[#4d4d4d]">
                {state.data.language || file.language || "text"}
              </span>
            )}
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              className="text-[#4d4d4d] transition-colors hover:text-[#00ff41] focus:outline-none focus-visible:ring-1 focus-visible:ring-[#00ff41]"
              aria-label="Close the file"
            >
              <X size={16} aria-hidden />
            </button>
          </span>
        </div>

        <div className="min-h-0 flex-1 overflow-auto">
          {state.kind === "loading" && (
            <p className="px-4 py-6 text-center text-xs text-[#4d4d4d]">Reading the file…</p>
          )}

          {state.kind === "error" && (
            <p className="flex items-start gap-2 px-4 py-6 text-xs text-red-400">
              <AlertTriangle size={14} className="mt-px shrink-0" aria-hidden />
              <span>{state.message}</span>
            </p>
          )}

          {state.kind === "ready" && (
            <>
              {truncatedAt && (
                <p className="border-b border-white/10 bg-[#111] px-4 py-1.5 text-[10px] text-amber-400/80">
                  {truncatedAt} of {state.data.size} bytes shown. This is a preview of a large
                  file.
                </p>
              )}
              {content === "" ? (
                <p className="px-4 py-6 text-center text-xs text-[#4d4d4d]">
                  This file is empty.
                </p>
              ) : (
                <pre className="px-4 py-3 text-[11px] leading-relaxed text-[#b8b8b8]">
                  <code>{content}</code>
                </pre>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * The text to render, and a note when it is not all of it.
 *
 * Two separate caps, because they defend against different things: bytes stops a
 * single enormous line from reaching the DOM at all, and lines stops a file that
 * is a million short lines from locking the tab up once it has.
 */
function useMemoPreview(
  state: { kind: "loading" } | { kind: "ready"; data: RepoFile } | { kind: "error"; message: string }
): { content: string; truncatedAt: string | null } {
  if (state.kind !== "ready") return { content: "", truncatedAt: null };

  const raw = state.data.content ?? "";
  let content = raw;
  let truncatedAt: string | null = null;

  if (raw.length > MAX_PREVIEW_BYTES) {
    content = raw.slice(0, MAX_PREVIEW_BYTES);
    truncatedAt = `${MAX_PREVIEW_BYTES.toLocaleString()}`;
  }

  const lines = content.split("\n");
  if (lines.length > MAX_PREVIEW_LINES) {
    const kept = lines.slice(0, MAX_PREVIEW_LINES).join("\n");
    const hidden = lines.length - MAX_PREVIEW_LINES;
    content = `${kept}\n… ${hidden.toLocaleString()} more lines not shown`;
    if (!truncatedAt) truncatedAt = `${MAX_PREVIEW_LINES.toLocaleString()} lines`;
  }

  return { content, truncatedAt };
}