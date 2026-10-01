"use client";

import { useState } from "react";
import { ArrowRight, Loader2, Play } from "lucide-react";

/**
 * The first thing anyone sees, so it has to say what the product does in one
 * sentence and offer the two ways in without an account.
 *
 * The decoration is CSS and SVG only - no WebGL here - so the first paint is
 * instant and the page still reads on a phone or with animation disabled.
 */
export function HeroSection({
  onGetStarted,
  onTryDemo,
  demoLabel = "Open a stored analysis",
  demoBusy = false,
  notice = "",
}: {
  onGetStarted: () => void;
  onTryDemo?: () => void;
  demoLabel?: string;
  demoBusy?: boolean;
  notice?: string;
}) {
  const [glow, setGlow] = useState({ x: 0, y: 0, on: false });

  // The spotlight follows the pointer but stops when it leaves, so the hero
  // never keeps a mousemove listener alive.
  const track = (event: React.MouseEvent<HTMLElement>) => {
    const box = event.currentTarget.getBoundingClientRect();
    setGlow({ x: event.clientX - box.left, y: event.clientY - box.top, on: true });
  };

  return (
    <section
      onMouseMove={track}
      onMouseLeave={() => setGlow((current) => ({ ...current, on: false }))}
      className="relative w-full overflow-hidden border-b border-white/10 bg-[#050505] px-6 pb-20 pt-32 md:pb-28 md:pt-40"
    >
      {/* Blueprint grid, fading out towards the edges. */}
      <div aria-hidden className="pointer-events-none absolute inset-0 grid-bg opacity-[0.35]" />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(900px circle at 50% 0%, rgba(0,255,65,0.12), transparent 65%)",
        }}
      />
      {glow.on && (
        <div
          aria-hidden
          className="pointer-events-none absolute h-96 w-96 -translate-x-1/2 -translate-y-1/2 rounded-full blur-3xl transition-opacity duration-500"
          style={{
            left: glow.x,
            top: glow.y,
            background: "radial-gradient(circle, rgba(0,255,65,0.14), transparent 70%)",
          }}
        />
      )}

      <div className="relative mx-auto flex w-full max-w-5xl flex-col items-center text-center">
        <p className="font-mono text-[10px] uppercase tracking-[0.35em] text-[#00ff41] sm:text-xs">
          NVIDIA Nemotron on Nebius Token Factory
        </p>

        <h1 className="mt-6 font-outfit text-4xl font-extrabold leading-[1.05] tracking-tight text-white sm:text-5xl lg:text-7xl">
          Read a codebase
          <br />
          <span className="text-[#00ff41] glow-text">before you own it.</span>
        </h1>

        <p className="mt-6 max-w-2xl text-base leading-relaxed text-[#9a9a9a] sm:text-lg">
          Paste a public repository. CodeLens parses it with Tree-sitter, builds the real
          import graph, ranks the files that actually hurt, checks what it claims against
          live sources, and hands back findings you can click through to a line.
        </p>

        <div className="mt-10 flex w-full flex-col items-center gap-3 sm:w-auto sm:flex-row">
          <button
            type="button"
            onClick={onGetStarted}
            className="glow-button inline-flex w-full items-center justify-center gap-2 rounded-sm bg-[#00ff41] px-8 py-4 text-sm font-extrabold tracking-wide text-black sm:w-auto"
          >
            Analyze a repository
            <ArrowRight size={18} aria-hidden />
          </button>

          {onTryDemo && (
            <button
              type="button"
              onClick={onTryDemo}
              disabled={demoBusy}
              className="inline-flex w-full items-center justify-center gap-2 rounded-sm border border-[#00ff41]/40 px-8 py-4 text-sm font-bold tracking-wide text-[#00ff41] transition-colors hover:bg-[#00ff41]/10 disabled:cursor-not-allowed disabled:opacity-60 sm:w-auto"
            >
              {demoBusy ? (
                <Loader2 size={16} className="animate-spin" aria-hidden />
              ) : (
                <Play size={16} aria-hidden />
              )}
              {demoBusy ? "Loading" : demoLabel}
            </button>
          )}
        </div>

        <p className="mt-5 font-mono text-[11px] text-[#4d4d4d]">
          No account, no signup. Rate limits apply.
        </p>

        {notice && (
          <p role="status" className="mt-4 max-w-lg text-sm text-amber-400/90">
            {notice}
          </p>
        )}
      </div>
    </section>
  );
}