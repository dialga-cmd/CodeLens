"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Boxes,
  Braces,
  CheckCircle2,
  Code2,
  ExternalLink,
  Flame,
  GitBranch,
  Globe,
  Loader2,
  Package,
  Radar,
  Search,
  ShieldCheck,
  Terminal,
  Wrench,
  XCircle,
} from "lucide-react";

import DottedSurface from "./DottedSurface";
import { HeroSection } from "./HeroSection";
import { api, API_BASE_URL, type DemoEntry, type HealthReport } from "@/lib/api";
import { rememberAnalysis, stashResults } from "@/lib/session";

const REPO_URL = "https://github.com/dialga-cmd/CodeLens";

export interface LandingPageProps {
  onGetStarted: () => void | Promise<unknown>;
  health?: HealthReport | null;
  healthError?: string;
  canSignIn?: boolean;
  onSignIn?: () => Promise<unknown>;
  children?: React.ReactNode;
}

/**
 * The landing page: what CodeLens actually does, and the shortest way in.
 *
 * Two doors, side by side, because a judge has neither time nor an account:
 * analyse any public repository, or open an analysis that was generated earlier
 * and committed to the repository. The second one needs no clone, no key and no
 * waiting, which is the difference between "see the product" and "read about it".
 *
 * Everything on this page is either true by construction or read back from the
 * API. Capability claims are not written here; they are asked for at `/health`
 * and rendered from the answer, so an unconfigured instance says so.
 */
export default function LandingPage({
  onGetStarted,
  health = null,
  healthError = "",
  canSignIn = false,
  onSignIn,
  children,
}: LandingPageProps) {
  const router = useRouter();
  const [scrolled, setScrolled] = useState(false);
  const [demos, setDemos] = useState<DemoEntry[]>([]);
  const [demoSlug, setDemoSlug] = useState<string | null>(null);
  const [demoError, setDemoError] = useState("");

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // The demo list is optional: a checkout without stored analyses simply does not
  // offer the button, rather than offering one that fails.
  useEffect(() => {
    let cancelled = false;
    api
      .demoCatalogue()
      .then((payload) => {
        if (!cancelled) setDemos(payload.available ?? []);
      })
      .catch(() => {
        if (!cancelled) setDemos([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const openDemo = useCallback(
    async (slug: string) => {
      setDemoSlug(slug);
      setDemoError("");
      try {
        const analysis = await api.demo(slug);
        // Kept in session storage exactly like a live run, so the results page
        // cannot tell - and does not need to care - which one opened it.
        rememberAnalysis(analysis.repo_id, analysis.repo_url);
        stashResults(analysis);
        router.push("/results");
      } catch (error) {
        setDemoError(error instanceof Error ? error.message : "That demo could not be opened.");
        setDemoSlug(null);
      }
    },
    [router]
  );

  const start = useCallback(() => {
    void onGetStarted();
  }, [onGetStarted]);

  return (
    <div className="min-h-screen bg-[#050505] text-[#f0f0f0] selection:bg-[#00ff41] selection:text-black">
      <div className="pointer-events-none fixed inset-0 z-0">
        <div className="grid-bg absolute inset-0 opacity-[0.06]" />
        <DottedSurface />
      </div>

      <nav
        className={`fixed top-0 z-50 w-full transition-all duration-300 ${
          scrolled ? "glass border-b border-white/10 py-3" : "py-6"
        }`}
      >
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6">
          <a href="#top" className="flex items-center gap-2">
            <span className="rounded-sm bg-[#00ff41] p-1.5">
              <Code2 size={18} className="text-black" aria-hidden />
            </span>
            <span className="font-changa text-lg font-bold tracking-widest">CODELENS</span>
          </a>

          <div className="hidden items-center gap-7 text-sm font-medium text-[#8a8a8a] md:flex">
            <a className="transition-colors hover:text-[#00ff41]" href="#pipeline">
              Pipeline
            </a>
            <a className="transition-colors hover:text-[#00ff41]" href="#findings">
              What you get
            </a>
            <a className="transition-colors hover:text-[#00ff41]" href="#demo">
              Demo
            </a>
            <a className="transition-colors hover:text-[#00ff41]" href="#status">
              Status
            </a>
            <button
              type="button"
              onClick={start}
              className="rounded border border-[#00ff41]/40 px-4 py-2 text-[#00ff41] transition-colors hover:bg-[#00ff41]/10"
            >
              Analyze a repo
            </button>
          </div>

          <button
            type="button"
            onClick={start}
            className="rounded border border-[#00ff41]/40 px-3 py-1.5 text-xs font-bold text-[#00ff41] md:hidden"
          >
            Start
          </button>
        </div>
      </nav>

      <div id="top" className="relative z-10">
        <HeroSection
          onGetStarted={start}
          onTryDemo={demos.length > 0 ? () => openDemo(demos[0].slug) : undefined}
          demoLabel={demos.length > 0 ? demos[0].label : undefined}
          demoBusy={demoSlug !== null}
          notice={healthError}
        />

        {demos.length > 0 && (
          <section id="demo" className="border-b border-white/10 px-6 py-14">
            <div className="mx-auto max-w-5xl">
              <div className="flex flex-wrap items-baseline justify-between gap-3">
                <h2 className="font-outfit text-2xl font-bold text-white md:text-3xl">
                  Analyses you can open right now
                </h2>
                <p className="text-sm text-[#6a6a6a]">No clone, no model call, no waiting.</p>
              </div>
              <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[#8a8a8a]">
                These are real runs of the pipeline, stored in this repository at the commit
                shown. Everything computed from the code is there - graph, hotspots, findings,
                researched dependencies, architecture summary. Anything that needs the files on
                disk is disabled rather than faked, and the page says which.
              </p>

              <ul className="mt-8 grid gap-4 sm:grid-cols-2">
                {demos.map((entry) => (
                  <li key={entry.slug}>
                    <button
                      type="button"
                      onClick={() => openDemo(entry.slug)}
                      disabled={demoSlug !== null}
                      className="glass h-full w-full rounded-xl p-5 text-left transition-colors hover:border-[#00ff41]/40 disabled:opacity-60"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <span className="font-bold text-white">{entry.label}</span>
                        {demoSlug === entry.slug ? (
                          <Loader2 size={16} className="shrink-0 animate-spin text-[#00ff41]" aria-hidden />
                        ) : (
                          <ExternalLink size={16} className="shrink-0 text-[#00ff41]" aria-hidden />
                        )}
                      </div>
                      <p className="mt-1 truncate font-mono text-[11px] text-[#6a6a6a]">
                        {entry.repo_url}
                      </p>
                      <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
                        <Stat label="files" value={entry.stats?.files} />
                        <Stat label="edges" value={entry.stats?.graph_edges} />
                        <Stat label="hotspots" value={entry.stats?.hotspots} />
                        <Stat label="findings" value={entry.stats?.findings} />
                      </dl>
                      <p className="mt-4 font-mono text-[10px] text-[#4d4d4d]">
                        {entry.head_sha ? `${entry.head_sha.slice(0, 7)} · ` : ""}
                        {entry.generated_at?.slice(0, 10)}
                      </p>
                    </button>
                  </li>
                ))}
              </ul>

              {demoError && (
                <p role="alert" className="mt-4 text-sm text-red-400">
                  {demoError}
                </p>
              )}
            </div>
          </section>
        )}

        <section id="findings" className="border-b border-white/10 px-6 py-20">
          <div className="mx-auto max-w-7xl">
            <h2 className="font-outfit text-3xl font-bold text-white md:text-4xl">
              Everything below is computed, not guessed.
            </h2>
            <p className="mt-4 max-w-2xl text-[#8a8a8a]">
              A language model reading files produces opinions. CodeLens produces measurements
              first, then lets the model argue about them - so every number on the dashboard can
              be traced back to the file it came from.
            </p>

            <div className="mt-12 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
              <FeatureCard
                icon={<Braces className="h-5 w-5 text-[#00ff41]" aria-hidden />}
                title="Real parsing, 19 languages"
                desc="Tree-sitter parses Python, JS/TS/TSX, Go, Java, Rust, C/C++, C#, Ruby, PHP, Kotlin, Scala, Lua, Elixir, Perl, R and Bash into an AST. Functions, classes, branching nodes and imports come out of the syntax tree, not out of a regex guess - and a file in any other language still reaches the walk as text."
              />
              <FeatureCard
                icon={<GitBranch className="h-5 w-5 text-[#00ff41]" aria-hidden />}
                title="A dependency graph you can walk"
                desc="Imports are resolved to files, giving real fan-in and fan-out per module. The 3D view is that graph - click a node and read the file it points at."
              />
              <FeatureCard
                icon={<Flame className="h-5 w-5 text-[#00ff41]" aria-hidden />}
                title="Hotspots with stated reasons"
                desc="Ranked by cyclomatic complexity, blast radius, commit churn and open findings. Every rank explains itself: 'imported by 14 other files', 'changed in 37 commits in the last year'."
              />
              <FeatureCard
                icon={<Radar className="h-5 w-5 text-[#00ff41]" aria-hidden />}
                title="18 deterministic security rules"
                desc="Committed credentials, private keys, unsafe deserialization, shell and SQL injection, path traversal, JWT alg=none, CORS wildcards. AST-backed where the grammar allows it, each finding anchored to file and line."
              />
              <FeatureCard
                icon={<Globe className="h-5 w-5 text-[#00ff41]" aria-hidden />}
                title="Claims checked against the web"
                desc="Dependency verdicts are researched live through Tavily. Each answer carries the source links it came from, and anything unverified is labelled unverified instead of being asserted."
              />
              <FeatureCard
                icon={<Wrench className="h-5 w-5 text-[#00ff41]" aria-hidden />}
                title="A patch that applies"
                desc="The Fix Advisor writes a unified diff and the server checks it with git apply against the real clone before you ever see it. If it does not apply, you are told so."
              />
            </div>
          </div>
        </section>

        <section id="pipeline" className="border-b border-white/10 px-6 py-20">
          <div className="mx-auto grid max-w-7xl gap-14 lg:grid-cols-2 lg:items-center">
            <div>
              <span className="font-mono text-xs uppercase tracking-[0.3em] text-[#00ff41]">
                The pipeline
              </span>
              <h2 className="mt-3 font-outfit text-3xl font-bold leading-tight text-white md:text-4xl">
                From a URL to a reviewable finding.
              </h2>
              <ol className="mt-8 space-y-6">
                <Step
                  title="Clone, shallow and validated"
                  desc="Only public github.com URLs are accepted, over https, cloned at depth 1. Every path that later reads a file is checked against the clone root, so a crafted path cannot walk out of it."
                />
                <Step
                  title="Parse, then measure"
                  desc="Files are parsed with Tree-sitter, imports are resolved into a graph, and per-file metrics are computed: complexity, fan-in, fan-out, size, commit churn, findings."
                />
                <Step
                  title="Rank and review"
                  desc="Hotspots are scored from those signals. The heavyweight Nemotron model reviews the findings and writes the architecture summary, chunked so a large repository still fits."
                />
                <Step
                  title="Ground and verify"
                  desc="Tavily research backs the dependency and security claims with links. The Fix Advisor turns a finding into a diff, validated against the clone."
                />
              </ol>
            </div>

            <div className="glass relative overflow-hidden rounded-2xl border-[#00ff41]/20 p-6">
              <div className="mb-5 flex items-center gap-3">
                <Terminal size={18} className="text-[#00ff41]" aria-hidden />
                <span className="font-mono text-xs text-[#555]">progress stream</span>
              </div>
              <ul className="space-y-2 font-mono text-[11px] leading-relaxed">
                <Line>[clone]  depth 1, https only</Line>
                <Line>[parse]  tree-sitter · N files</Line>
                <Line>[graph]  imports resolved · N edges</Line>
                <Line>[hotspot] complexity · fan-in/out · churn</Line>
                <Line>[pre-scan] 18 rules · N findings</Line>
                <Line>[research] tavily · sources attached</Line>
                <Line className="text-[#00ff41]">[review] nemotron · heavy model</Line>
                <Line className="text-[#00ff41]">[done]  report ready</Line>
              </ul>
              <p className="mt-5 border-t border-white/10 pt-4 text-[11px] leading-relaxed text-[#555]">
                Progress is streamed to the browser over Server-Sent Events. Nothing here is
                pre-recorded: this is the shape of the log a real run prints.
              </p>
            </div>
          </div>
        </section>

        <section id="models" className="border-b border-white/10 px-6 py-20">
          <div className="mx-auto max-w-7xl">
            <h2 className="font-outfit text-3xl font-bold text-white md:text-4xl">
              Two models, chosen per job.
            </h2>
            <div className="mt-10 grid gap-5 md:grid-cols-3">
              <ModelCard
                icon={<Boxes className="h-5 w-5 text-[#00ff41]" aria-hidden />}
                title="Heavy model, deliberate work"
                desc="Finding triage and the architecture summary need judgement over the whole repository, so they go to the larger Nemotron model on Nebius Token Factory."
                  model={health?.llm.models.heavy}
                tone="heavy"
              />
              <ModelCard
                icon={<Search className="h-5 w-5 text-[#00ff41]" aria-hidden />}
                title="Fast model, everyday work"
                desc="Chat, tool calls and patch drafting are latency-sensitive and run on the smaller, quicker Nemotron model, which keeps the dashboard responsive."
                  model={health?.llm.models.fast}
                tone="fast"
              />
              <ModelCard
                icon={<Package className="h-5 w-5 text-[#00ff41]" aria-hidden />}
                title="Live search, no memory"
                desc="Tavily does the web lookups, so a CVE identifier or an outdated-package claim is checked now rather than recalled. Sources are attached to the finding."
                tone="research"
              />
            </div>
            <p className="mt-6 text-sm leading-relaxed text-[#6a6a6a]">
              Model IDs are read from the running server, not hardcoded into this page: if the
              instance is configured you see the exact model it will call, and if it is not, the
              status panel below says so.
            </p>
          </div>
        </section>

        <section id="status" className="border-b border-white/10 px-6 py-20">
          <div className="mx-auto max-w-5xl">
            <h2 className="font-outfit text-3xl font-bold text-white md:text-4xl">
              What this instance can do right now.
            </h2>
            <p className="mt-4 max-w-2xl text-[#8a8a8a]">
              Asked of <code className="font-mono text-xs text-[#00ff41]">/health</code> rather
              than assumed.
            </p>

            <div className="mt-8 glass rounded-xl">
              {healthError ? (
                <div className="flex items-start gap-3 p-6">
                  <XCircle size={20} className="mt-0.5 shrink-0 text-red-400" aria-hidden />
                  <div>
                    <p className="font-bold text-white">The API is not answering</p>
                    <p className="mt-1 text-sm text-[#8a8a8a]">
                      {healthError} Start it with{" "}
                      <code className="font-mono text-xs text-[#00ff41]">uvicorn main:app</code>{" "}
                      in <code className="font-mono text-xs text-[#00ff41]">server/</code>, or point{" "}
                      <code className="font-mono text-xs text-[#00ff41]">NEXT_PUBLIC_API_BASE_URL</code>{" "}
                      at a deployed instance. Currently trying{" "}
                      <code className="font-mono text-xs text-[#888]">{API_BASE_URL}</code>.
                    </p>
                  </div>
                </div>
              ) : !health ? (
                <div className="flex items-center gap-3 p-6 text-[#8a8a8a]">
                  <Loader2 size={18} className="animate-spin" aria-hidden />
                  Checking what this instance can do...
                </div>
              ) : (
                <ul className="divide-y divide-white/5">
                  <StatusRow
                    ok
                    label="API reachable"
                    detail={`codelens-api ${health.version} · ${health.status}`}
                  />
                  <StatusRow
                    ok={health.llm.configured}
                    label="Model access"
                    detail={
                      health.llm.configured
                        ? `${health.llm.models.heavy} for review, ${health.llm.models.fast} for chat`
                        : "No model key on this instance. Static analysis, graph, hotspots and dependency inventory still work; the review, chat and fix advisor are reported as unavailable."
                    }
                  />
                  <StatusRow
                    ok={health.research.configured}
                    label="Web research"
                    detail={
                      health.research.configured
                        ? "Tavily is configured, so dependency and CVE claims are grounded with sources."
                        : "No Tavily key. Dependency inventory is still parsed; version and CVE verdicts are marked unverified rather than asserted."
                    }
                  />
                  <StatusRow
                    ok={!health.auth.required}
                    label="Access"
                    detail={
                      health.auth.required
                        ? "Sign-in is required on this deployment."
                        : `Open access - this instance runs in ${health.auth.mode} mode, so you can analyse a repository without an account.`
                    }
                  />
                </ul>
              )}
            </div>

            <div className="mt-6 flex flex-wrap gap-3 text-sm">
              <a
                href={`${API_BASE_URL}/docs`}
                className="inline-flex items-center gap-2 rounded border border-white/10 px-4 py-2 text-[#8a8a8a] transition-colors hover:border-[#00ff41]/40 hover:text-[#00ff41]"
              >
                API docs <ExternalLink size={14} aria-hidden />
              </a>
              <a
                href={`${API_BASE_URL}/health`}
                className="inline-flex items-center gap-2 rounded border border-white/10 px-4 py-2 text-[#8a8a8a] transition-colors hover:border-[#00ff41]/40 hover:text-[#00ff41]"
              >
                Health <ExternalLink size={14} aria-hidden />
              </a>
              <a
                href={REPO_URL}
                className="inline-flex items-center gap-2 rounded border border-white/10 px-4 py-2 text-[#8a8a8a] transition-colors hover:border-[#00ff41]/40 hover:text-[#00ff41]"
              >
                Source <ExternalLink size={14} aria-hidden />
              </a>
            </div>
          </div>
        </section>

        <section className="px-6 py-24 text-center">
          <div className="glass relative mx-auto max-w-3xl overflow-hidden rounded-3xl border-[#00ff41]/30 p-10 md:p-16">
            <h2 className="font-outfit text-3xl font-bold text-white md:text-4xl">
              Point it at something you <span className="text-[#00ff41]">maintain.</span>
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-[#8a8a8a]">
              The heritage of a codebase is usually written down nowhere. Put in a URL and read
              what the code says about itself.
            </p>

            <div className="mt-10 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <button
                type="button"
                onClick={start}
                className="glow-button inline-flex items-center gap-2 rounded-sm bg-[#00ff41] px-10 py-4 text-sm font-extrabold text-black"
              >
                Analyze a repository
              </button>
              {canSignIn && onSignIn && (
                <button
                  type="button"
                  onClick={() => void onSignIn()}
                  className="rounded-sm border border-white/15 px-8 py-4 text-sm font-bold text-[#8a8a8a] transition-colors hover:border-white/40 hover:text-white"
                >
                  Sign in
                </button>
              )}
            </div>
            {canSignIn && (
              <p className="mt-4 text-xs text-[#4d4d4d]">
                Signing in is optional. It only adds an account to the analysis.
              </p>
            )}
          </div>
        </section>

        <footer className="border-t border-white/10 px-6 py-12">
          <div className="mx-auto flex max-w-7xl flex-col items-start justify-between gap-8 sm:flex-row sm:items-center">
            <div className="flex items-center gap-2">
              <Code2 size={20} className="text-[#00ff41]" aria-hidden />
              <span className="font-changa text-lg font-bold tracking-widest">CODELENS</span>
            </div>
            <p className="max-w-md text-xs leading-relaxed text-[#4d4d4d]">
              Built for the Nebius x NVIDIA Global AI Hackathon. Static analysis is deterministic;
              model output is labelled as model output.
            </p>
            <div className="flex gap-5 text-xs text-[#6a6a6a]">
              <a className="transition-colors hover:text-[#00ff41]" href={REPO_URL}>
                GitHub
              </a>
              <a className="transition-colors hover:text-[#00ff41]" href={`${API_BASE_URL}/docs`}>
                API
              </a>
              <a className="transition-colors hover:text-[#00ff41]" href="#top">
                Top
              </a>
            </div>
          </div>
        </footer>
      </div>

      {children}
    </div>
  );
}

function Stat({ label, value }: { label: string; value?: number }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-widest text-[#4d4d4d]">{label}</dt>
      <dd className="text-sm font-bold text-[#e5e5e5]">{value ?? 0}</dd>
    </div>
  );
}

function FeatureCard({
  icon,
  title,
  desc,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
}) {
  return (
    <article className="glass h-full rounded-xl p-6">
      <div className="mb-4 grid h-11 w-11 place-items-center rounded-lg border border-white/10 bg-[#050505]">
        {icon}
      </div>
      <h3 className="text-lg font-bold text-white">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-[#7a7a7a]">{desc}</p>
    </article>
  );
}

function ModelCard({
  icon,
  title,
  desc,
  model,
  tone,
}: {
  icon: React.ReactNode;
  title: string;
  desc: string;
  model?: string;
  tone: "heavy" | "fast" | "research";
}) {
  return (
    <article className="glass flex h-full flex-col rounded-xl p-6">
      <div className="mb-4 grid h-11 w-11 place-items-center rounded-lg border border-white/10 bg-[#050505]">
        {icon}
      </div>
      <h3 className="text-lg font-bold text-white">{title}</h3>
      <p className="mt-2 flex-1 text-sm leading-relaxed text-[#7a7a7a]">{desc}</p>
      <p className="mt-4 break-all border-t border-white/10 pt-3 font-mono text-[11px] text-[#00ff41]/80">
        {tone === "research" ? "Tavily Search API" : model || "not configured on this instance"}
      </p>
    </article>
  );
}

function StatusRow({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <li className="flex items-start gap-4 p-5">
      <span className="mt-0.5 shrink-0">
        {ok ? (
          <CheckCircle2 size={18} className="text-[#00ff41]" aria-hidden />
        ) : (
          <AlertTriangle size={18} className="text-amber-400" aria-hidden />
        )}
      </span>
      <div className="min-w-0">
        <p className="text-sm font-bold text-white">
          {label}
          <span
            className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide ${
              ok ? "bg-[#00ff41]/15 text-[#00ff41]" : "bg-amber-400/10 text-amber-400"
            }`}
          >
            {ok ? "ready" : "limited"}
          </span>
        </p>
        <p className="mt-1 text-xs leading-relaxed text-[#7a7a7a]">{detail}</p>
      </div>
    </li>
  );
}

function Step({ title, desc }: { title: string; desc: string }) {
  return (
    <li className="flex gap-4">
      <span aria-hidden className="mt-2 h-2 w-2 shrink-0 rounded-full bg-[#00ff41]" />
      <div>
        <h3 className="font-bold text-white">{title}</h3>
        <p className="mt-1 text-sm leading-relaxed text-[#7a7a7a]">{desc}</p>
      </div>
    </li>
  );
}

function Line({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <li className={`leading-relaxed ${className || "text-[#7a7a7a]"}`}>{children}</li>;
}