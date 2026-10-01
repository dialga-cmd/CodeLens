"use client";

import React, { useEffect, useRef, useState } from "react";
import { Search, Loader2 } from "lucide-react";
import { motion } from "framer-motion";

/**
 * The repository input: one field, one action, keyboard accessible.
 *
 * The field is a real `<form>` with a labelled input and a submit button, so
 * Enter works, a screen reader announces the label, and the error state below it
 * is announced too - none of which a div with an onClick would give.
 */
export function GlowingInput({
  label = "Public GitHub repository URL",
  placeholder = "https://github.com/pallets/flask",
  value,
  onChange,
  onSubmit,
  disabled = false,
  busy = false,
  error = "",
  id = "codelens-repo-input",
}: {
  label?: string;
  placeholder?: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: (value: string) => void;
  disabled?: boolean;
  busy?: boolean;
  error?: string;
  id?: string;
}) {
  const [focused, setFocused] = useState(false);
  const errorId = `${id}-error`;

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmed = value.trim();
    if (trimmed && !disabled && !busy) onSubmit(trimmed);
  };

  return (
    <form onSubmit={submit} className="w-full" noValidate>
      <label htmlFor={id} className="sr-only">
        {label}
      </label>

      <motion.div
        animate={{
          boxShadow: focused
            ? "0 0 110px -30px rgba(0,255,65,0.75)"
            : "0 0 70px -35px rgba(0,255,65,0.5)",
        }}
        transition={{ type: "spring", stiffness: 80, damping: 22 }}
        className="relative flex items-center gap-3 w-full px-4 py-3 md:px-5 rounded-full bg-gradient-to-r from-slate-950 to-slate-900 ring-1 ring-white/10"
      >
        <span
          aria-hidden
          className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-white/5 ring-1 ring-white/10"
        >
          <Search className="h-4 w-4 text-[#00ff41]" />
        </span>

        <input
          id={id}
          type="url"
          inputMode="url"
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          placeholder={placeholder}
          disabled={disabled || busy}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          autoComplete="off"
          spellCheck={false}
          className="min-w-0 flex-1 bg-transparent text-slate-100 outline-none text-base md:text-lg placeholder:text-slate-500 disabled:opacity-60"
        />

        <button
          type="submit"
          disabled={!value.trim() || disabled || busy}
          aria-label={busy ? "Analysis in progress" : "Analyze repository"}
          className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-[#00ff41] text-black ring-4 ring-[#00ff41]/20 transition-transform hover:scale-105 focus:outline-none focus-visible:ring-4 focus-visible:ring-white/60 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Search className="h-4 w-4" />
          )}
        </button>
      </motion.div>

      {/* Announced by screen readers when it appears, and visible either way. */}
      <p
        id={errorId}
        role={error ? "alert" : undefined}
        className={`mt-3 text-sm ${error ? "text-red-400" : "text-transparent h-0 overflow-hidden"}`}
      >
        {error || "placeholder"}
      </p>
    </form>
  );
}

/** Legacy alias kept so existing imports keep working. */
export const PromptInput = GlowingInput;

export default GlowingInput;