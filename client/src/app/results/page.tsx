"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  Check,
  ChevronDown,
  Code2,
  Cpu,
  FileCode,
  Flame,
  GitBranch,
  Globe,
  Loader2,
  Package,
  Search,
  ShieldAlert,
  Terminal,
  Wrench,
  X,
} from "lucide-react";

import GraphView from "@/components/GraphView";
import AIChat from "@/components/AIChat";
import { useAuth } from "@/hooks/useAuth";
import { api, type AnalysisResult, type Finding, type ProposedFix, type RepoFile } from "@/lib/api";
import { lastAnalysis, readStashedResults, stashResults } from "@/lib/session";

type LoadState = "loading" | "ready" | "missing";

/**
 * The report.
 *
 * Everything here is read back from one analysis object, which can arrive three
 * ways: straight off the analysis stream, out of session storage after a
 * navigation, or re-fetched from the API by repository id when the URL is
 * reloaded. Whichever it is, the page says which, because a report that cannot
 * say how fresh it is is not evidence of anything.
 */
export default function ResultsPage() {
  const { getIdToken, logout, user, isGuest } = useAuth();
  const router = useRouter();

  const [results, setResults] = useState<AnalysisResult | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [loadError, setLoadError] = useState("");
  const [origin, setOrigin] = useState<"stream" | "stored" | "server">("stream");
  const [tab, setTab] = useState<"overview" | "findings" | "dependencies">("overview");

  // The id is remembered so a reload can ask the API for the same analysis.
  const { repoId } = useMemo(() => lastAnalysis(), []);

  useEffect(() => {
    let cancelled = false;

    const stashed = readStashedResults<AnalysisResult>();
    if (stashed?.repo_id) {
      setResults(stashed);
      setOrigin("stream");
      setState("ready");
      return;
    }

    if (!repoId) {
      setState("missing");
      return;
    }

    api
      .analysis(repoId, getIdToken)
      .then((analysis) => {
        if (cancelled) return;
        stashResults(analysis);
        setResults(analysis);
        setOrigin("server");
        setState("ready");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setLoadError(error instanceof Error ? error.message : "That analysis could not be loaded.");
        setState("missing");
      });

    return () => {
      cancelled = true;
    };
  }, [repoId, getIdToken]);

  const startOver = useCallback(() => {
    router.push("/dashboard");
  }, [router]);

  if (state === "loading") {
    return <Splash label="Loading analysis" />;
  }

  if (state === "missing" || !results) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-6 bg-[#050505] px-6 text-center">
        <AlertTriangle size={32} className="text-amber-400" aria-hidden />
        <div>
          <h1 className="text-xl font-bold">No analysis to show</h1>
          <p className="mt-2 max-w-md text-sm text-[#8a8a8a]">
            {loadError
              ? loadError
              : "Nothing is stored for this browser yet. Point CodeLens at a repository and it will show up here."}
          </p>
        </div>
        <button
          type="button"
          onClick={startOver}
          className="glow-button inline-flex items-center gap-2 rounded-sm bg-[#00ff41] px-6 py-3 text-sm font-extrabold text-black"
        >
          Analyze a repository
        </button>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#050505] text-[#f0f0f0]">
      <ReportHeader
        results={results}
        origin={origin}
        isGuest={isGuest}
        signedInAs={user?.displayName || user?.email || ""}
        onStartOver={startOver}
        onSignOut={async () => {
          await logout();
          router.push("/");
        }}
      />

      {results.demo && (
        <div className="border-b border-amber-500/20 bg-amber-500/5 px-6 py-3 text-xs text-amber-200/90">
          <strong className="font-bold">Stored analysis.</strong> This is a real run of the
          pipeline, kept in the repository at commit{" "}
          <code className="font-mono">{results.demo.head_sha?.slice(0, 7) || "unknown"}</code>.{" "}
          {results.demo.live_required_for?.length
            ? `${results.demo.live_required_for.join(", ")} need a live run`
            : "Everything here needs a live run"}{" "}
          to show, because the clone is not part of the stored analysis - run it yourself for the
          full experience.
        </div>
      )}

      {results.cached && origin === "server" && (
        <div className="border-b border-white/10 bg-[#0a0a0a] px-6 py-2 text-xs text-[#6a6a6a]">
          Re-opened from the stored snapshot. The analysis did not run again.
        </div>
      )}

      <nav className="flex gap-1 overflow-x-auto border-b border-white/10 px-4 md:px-6">
        {(
          [
            ["overview", "Overview", Cpu],
            ["findings", "Findings", ShieldAlert],
            ["dependencies", "Dependencies", Package],
          ] as const
        ).map(([key, label, Icon]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            aria-current={tab === key ? "page" : undefined}
            className={`flex shrink-0 items-center gap-2 border-b-2 px-4 py-3 text-sm transition-colors ${
              tab === key
                ? "border-[#00ff41] text-[#00ff41]"
                : "border-transparent text-[#7a7a7a] hover:text-white"
            }`}
          >
            <Icon size={15} aria-hidden />
            {label}
          </button>
        ))}
      </nav>

      <div className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_380px] lg:p-6">
        <div className="min-w-0 space-y-4">
          {tab === "overview" && (
            <>
              <GraphPanel results={results} />
              <ChatPanel results={results} getIdToken={getIdToken} isDemo={Boolean(results.demo)} />
            </>
          )}

          {tab === "findings" && (
            <FindingsPanel
              results={results}
              getIdToken={getIdToken}
              isDemo={Boolean(results.demo)}
            />
          )}

          {tab === "dependencies" && (
            <>
              <DependenciesPanel results={results} />
              <ChatPanel results={results} getIdToken={getIdToken} isDemo={Boolean(results.demo)} />
            </>
          )}
        </div>

        <aside className="min-w-0 space-y-4">
          <OverviewPanel results={results} />
          <FindingsSummary results={results} />
          {tab === "overview" && (
            <>
              <ArchitecturePanel results={results} />
              <HotspotsPanel results={results} />
              <DependenciesPanel results={results} compact />
            </>
          )}
          {tab === "findings" && (
            <ChatPanel results={results} getIdToken={getIdToken} isDemo={Boolean(results.demo)} />
          )}
          {tab === "dependencies" && <HotspotsPanel results={results} />}
        </aside>
      </div>
    </main>
  );
}

// --------------------------------------------------------------------------- //
// header
// --------------------------------------------------------------------------- //

function ReportHeader({
  results,
  origin,
  isGuest,
  signedInAs,
  onStartOver,
  onSignOut,
}: {
  results: AnalysisResult;
  origin: "stream" | "stored" | "server";
  isGuest: boolean;
  signedInAs: string;
  onStartOver: () => void;
  onSignOut: () => Promise<void>;
}) {
  const stats = results.stats || {};
  const label =
    origin === "stream" ? "fresh run" : origin === "server" ? "re-opened" : "restored";

  return (
    <header className="flex flex-wrap items-start justify-between gap-4 border-b border-white/10 px-4 py-4 md:px-6">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <Code2 className="text-[#00ff41]" size={20} aria-hidden />
          <h1 className="text-lg font-bold tracking-tight">Analysis</h1>
          <span className="rounded border border-white/10 px-2 py-0.5 text-[10px] uppercase tracking-widest text-[#6a6a6a]">
            {label}
          </span>
          {results.head_sha && (
            <span className="font-mono text-[10px] text-[#4d4d4d]">
              {results.head_sha.slice(0, 7)}
            </span>
          )}
        </div>
        <a
          href={results.repo_url}
          target="_blank"
          rel="noreferrer"
          className="mt-1 block max-w-full truncate font-mono text-xs text-[#6a6a6a] hover:text-[#00ff41]"
        >
          {results.repo_url}
        </a>
        <p className="mt-1 text-[11px] text-[#4d4d4d]">
          {isGuest
            ? "Analysed as a guest"
            : `Analysed as ${signedInAs || "a signed-in user"}`}
          {" · "}
          {stats.total_files ?? 0} files · {stats.total_vulnerabilities ?? 0} findings ·{" "}
          {stats.hotspot_count ?? 0} hotspots
        </p>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onStartOver}
          className="inline-flex items-center gap-2 rounded border border-white/10 px-3 py-2 text-xs text-[#8a8a8a] transition-colors hover:border-[#00ff41]/40 hover:text-[#00ff41]"
        >
          <ArrowLeft size={14} aria-hidden /> New analysis
        </button>
        {!isGuest && (
          <button
            type="button"
            onClick={() => void onSignOut()}
            className="rounded border border-white/10 px-3 py-2 text-xs text-[#8a8a8a] transition-colors hover:border-red-500/40 hover:text-red-400"
          >
            Sign out
          </button>
        )}
      </div>
    </header>
  );
}

function Splash({ label }: { label: string }) {
  return (
    <main className="flex min-h-screen items-center justify-center gap-3 bg-[#050505] text-[#00ff41]">
      <Loader2 className="animate-spin" size={20} aria-hidden />
      <span className="text-sm">{label}...</span>
    </main>
  );
}

// --------------------------------------------------------------------------- //
// panels
// --------------------------------------------------------------------------- //

function Panel({
  title,
  icon,
  children,
  actions,
}: {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <section className="panel overflow-hidden">
      <div className="flex items-center gap-2 border-b border-white/10 bg-[#111] px-4 py-3">
        {icon}
        <h2 className="text-xs font-bold uppercase tracking-widest text-[#8a8a8a]">{title}</h2>
        {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  );
}

function GraphPanel({ results }: { results: AnalysisResult }) {
  const [selected, setSelected] = useState<{ path: string; language: string } | null>(null);

  return (
    <Panel
      title="Dependency graph"
      icon={<GitBranch size={14} className="text-[#00ff41]" aria-hidden />}
      actions={<span className="hidden text-[10px] text-[#4d4d4d] sm:inline">click a file</span>}
    >
      <div className="h-[420px] bg-[#0a0a0a] md:h-[520px]">
        <GraphView
          data={results.graph}
          analysedFiles={results.stats?.total_files}
          onNodeClick={(node: any) =>
            setSelected({
              path: String(node?.path || node?.id || ""),
              language: String(node?.language || ""),
            })
          }
        />
      </div>
      {selected && (
        <div className="flex items-center justify-between gap-3 border-t border-white/10 px-4 py-2 text-xs">
          <span className="min-w-0 truncate font-mono text-[#8a8a8a]">{selected.path}</span>
          <span className="flex shrink-0 items-center gap-2">
            <span className="text-[10px] uppercase tracking-widest text-[#4d4d4d]">
              {selected.language}
            </span>
            <button
              type="button"
              onClick={() => setSelected(null)}
              className="text-[#4d4d4d] hover:text-[#00ff41]"
            >
              clear
            </button>
          </span>
        </div>
      )}
    </Panel>
  );
}

function OverviewPanel({ results }: { results: AnalysisResult }) {
  const stats = results.stats || {};
  const critical = (stats.critical_vulnerabilities || 0) + (stats.high_vulnerabilities || 0);

  const cards: Array<{ label: string; value: number | string; note: string; tone?: string }> = [
    { label: "Files analysed", value: stats.total_files ?? 0, note: "read from the clone" },
    { label: "Import edges", value: stats.graph_links ?? 0, note: "resolved to real files" },
    {
      label: "Findings",
      value: stats.total_vulnerabilities ?? 0,
      note: `${critical} critical or high`,
      tone: critical ? "text-red-400" : "",
    },
    { label: "Hotspots", value: stats.hotspot_count ?? 0, note: "ranked by measured signal" },
    { label: "Dependencies", value: stats.dependencies ?? 0, note: "parsed from manifests" },
    { label: "Languages", value: Object.keys(stats.languages || {}).length, note: "by file count" },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
      {cards.map((card) => (
        <div key={card.label} className="panel p-4">
          <p className="text-[10px] uppercase tracking-widest text-[#4d4d4d]">{card.label}</p>
          <p className={`mt-1 text-2xl font-bold ${card.tone || "text-white"}`}>{card.value}</p>
          <p className="mt-1 text-[10px] text-[#5a5a5a]">{card.note}</p>
        </div>
      ))}
    </div>
  );
}

function FindingsSummary({ results }: { results: AnalysisResult }) {
  const findings = results.vulnerabilities || [];
  const dismissed = useMemo(
    () => findings.filter((finding) => finding.triage === "dismissed"),
    [findings],
  );
  const live = useMemo(
    () => findings.filter((finding) => finding.triage !== "dismissed"),
    [findings],
  );
  const bySeverity = useMemo(() => {
    const counts = new Map<string, number>();
    for (const finding of live) {
      const key = String(finding.severity || "UNKNOWN").toUpperCase();
      counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [live]);

  const summary = results.security_summary || {};

  return (
    <Panel
      title="Security pre-scan"
      icon={<ShieldAlert size={14} className="text-red-400" aria-hidden />}
      actions={
        <span className="text-[10px] text-[#4d4d4d]">
          {live.length} counted
          {dismissed.length > 0 && `, ${dismissed.length} dismissed`}
        </span>
      }
    >
      <div className="space-y-4 p-4">
        {findings.length === 0 ? (
          <p className="text-xs text-[#5a5a5a]">
            No rule matched. That means these 18 patterns are not present, not that the code is
            safe - the checks are deterministic pattern matches and a model reviews them.
          </p>
        ) : (
          <>
            <ul className="flex flex-wrap gap-2">
              {bySeverity.map(([severity, count]) => (
                <li key={severity}>
                  <SeverityBadge severity={severity} count={count} />
                </li>
              ))}
            </ul>
            {dismissed.length > 0 && (
              <p className="text-[11px] leading-relaxed text-[#5a5a5a]">
                {dismissed.length} further {dismissed.length === 1 ? "match" : "matches"} from the
                same rules were reviewed by the model and dismissed as false positives, so they are
                not counted above. They stay in the list below with the reason, because a dismissal
                is a judgement you can disagree with.
              </p>
            )}
          </>
        )}

        {summary && Object.keys(summary).length > 0 && (
          <dl className="space-y-1 border-t border-white/5 pt-3 text-[11px]">
            {Object.entries(summary)
              .filter(([, value]) => typeof value === "string" || typeof value === "number")
              .slice(0, 6)
              .map(([key, value]) => (
                <div key={key} className="flex justify-between gap-3">
                  <dt className="text-[#5a5a5a]">{key.replace(/_/g, " ")}</dt>
                  <dd className="text-right font-mono text-[#8a8a8a]">{String(value)}</dd>
                </div>
              ))}
          </dl>
        )}

        {findings.some((finding) => finding.triage && finding.triage !== "pending") && (
          <p className="border-t border-white/5 pt-3 text-[11px] leading-relaxed text-[#5a5a5a]">
            Some findings were triaged by the model. That is the model's judgement about
            reachability and impact; the pattern match itself was made by the static rules.
          </p>
        )}
      </div>
    </Panel>
  );
}

function FindingsPanel({
  results,
  getIdToken,
  isDemo,
}: {
  results: AnalysisResult;
  getIdToken: () => Promise<string | null>;
  isDemo: boolean;
}) {
  const findings = results.vulnerabilities || [];
  const [selected, setSelected] = useState<Finding | null>(null);
  const [filter, setFilter] = useState("all");

  const severities = useMemo(
    () => [...new Set(findings.map((finding) => String(finding.severity || "").toUpperCase()))],
    [findings]
  );
  const shown = filter === "all" ? findings : findings.filter((f) => String(f.severity).toUpperCase() === filter);

  return (
    <Panel
      title={`Findings · ${findings.length}`}
      icon={<ShieldAlert size={14} className="text-red-400" aria-hidden />}
      actions={
        <div className="flex gap-1">
          {["all", ...severities.slice(0, 3)].map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setFilter(key)}
              className={`rounded px-2 py-1 text-[10px] uppercase tracking-wide transition-colors ${
                filter === key
                  ? "bg-[#00ff41]/15 text-[#00ff41]"
                  : "text-[#5a5a5a] hover:text-white"
              }`}
            >
              {key}
            </button>
          ))}
        </div>
      }
    >
      {shown.length === 0 ? (
        <p className="p-6 text-center text-xs text-[#5a5a5a]">
          {findings.length === 0
            ? "No rule matched this repository. The model still reviews the code, and the result is in the chat."
            : "Nothing with that severity."}
        </p>
      ) : (
        <ul className="divide-y divide-white/5">
          {shown.map((finding, index) => (
            <li key={finding.id || `${finding.file_path}:${finding.line}:${index}`}>
              <button
                type="button"
                onClick={() => setSelected(finding)}
                className="flex w-full items-start justify-between gap-3 p-4 text-left transition-colors hover:bg-white/[0.02]"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-[#e5e5e5]">{finding.name}</p>
                  <p className="mt-1 truncate font-mono text-[11px] text-[#5a5a5a]">
                    {finding.file_path}:{finding.line}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {finding.research?.sources?.length ? (
                    <Globe size={13} className="text-sky-400" aria-label="has web sources" />
                  ) : null}
                  {finding.triage && finding.triage !== "pending" && (
                    <span className="rounded bg-[#00ff41]/10 px-1.5 py-0.5 text-[9px] font-bold uppercase text-[#00ff41]">
                      {finding.triage}
                    </span>
                  )}
                  <SeverityBadge severity={String(finding.severity || "").toUpperCase()} />
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected && (
        <FindingDetail
          finding={selected}
          repoId={results.repo_id}
          getIdToken={getIdToken}
          isDemo={isDemo}
          onClose={() => setSelected(null)}
        />
      )}
    </Panel>
  );
}

/** One finding in full: why it matched, what the model said, and the patch. */
function FindingDetail({
  finding,
  repoId,
  getIdToken,
  isDemo,
  onClose,
}: {
  finding: Finding;
  repoId: string;
  getIdToken: () => Promise<string | null>;
  isDemo: boolean;
  onClose: () => void;
}) {
  const [fix, setFix] = useState<ProposedFix | null>(null);
  const [busy, setBusy] = useState(false);
  const [fixError, setFixError] = useState("");

  const askForFix = async () => {
    setBusy(true);
    setFixError("");
    setFix(null);
    try {
      const payload = await api.proposeFix(
        {
          repo_id: repoId,
          finding_id: finding.id,
          file_path: finding.file_path,
          line: Number(finding.line) || 0,
        },
        getIdToken
      );
      if (payload.error) {
        setFixError(payload.error);
      } else if (payload.fix) {
        setFix(payload.fix);
      } else {
        setFixError("The advisor returned no patch for this finding.");
      }
    } catch (error) {
      setFixError(error instanceof Error ? error.message : "Patch generation failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="border-t border-white/10 bg-[#0a0a0a]">
      <div className="flex items-start justify-between gap-3 border-b border-white/5 p-4">
        <div className="min-w-0">
          <h3 className="text-sm font-bold text-white">{finding.name}</h3>
          <p className="mt-1 break-all font-mono text-[11px] text-[#5a5a5a]">
            {finding.file_path}:{finding.line}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close finding"
          className="shrink-0 rounded border border-white/10 p-1.5 text-[#5a5a5a] hover:text-[#00ff41]"
        >
          <X size={14} />
        </button>
      </div>

      <div className="space-y-4 p-4 text-xs leading-relaxed">
        <p className="text-[#a0a0a0]">{finding.description}</p>

        {finding.snippet && (
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded border border-white/10 bg-[#050505] p-3 font-mono text-[11px] text-[#8a8a8a]">
            {finding.snippet}
          </pre>
        )}

        {finding.recommendation && (
          <p className="border-l-2 border-[#00ff41]/40 pl-3 text-[#8a8a8a]">
            <strong className="text-[#e5e5e5]">Fix:</strong> {finding.recommendation}
          </p>
        )}

        {finding.triage && finding.triage !== "pending" && (
          <div className="rounded border border-white/10 p-3">
            <p className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-[#4d4d4d]">
              <Bot size={12} aria-hidden /> Model triage: {finding.triage}
            </p>
            {finding.severity_reason && (
              <p className="mt-1 text-[#8a8a8a]">{finding.severity_reason}</p>
            )}
            <p className="mt-2 text-[10px] text-[#4d4d4d]">
              Model judgement. The detection below it was made by the static rules.
            </p>
          </div>
        )}

        {finding.research && (finding.research.answer || finding.research.sources?.length) && (
          <div className="rounded border border-sky-500/20 bg-sky-500/5 p-3">
            <p className="flex items-center gap-2 text-[10px] uppercase tracking-widest text-sky-300">
              <Globe size={12} aria-hidden /> Checked against the web
            </p>
            {finding.research.answer && (
              <p className="mt-1 text-[#a0a0a0]">{finding.research.answer}</p>
            )}
            <SourceLinks sources={finding.research.sources || []} />
          </div>
        )}

        <div className="border-t border-white/5 pt-3">
          {fix ? (
            <FixView fix={fix} />
          ) : (
            <>
              <button
                type="button"
                onClick={() => void askForFix()}
                disabled={busy || isDemo}
                className="inline-flex items-center gap-2 rounded border border-[#00ff41]/40 px-3 py-2 text-xs font-bold text-[#00ff41] transition-colors hover:bg-[#00ff41]/10 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {busy ? (
                  <Loader2 size={14} className="animate-spin" aria-hidden />
                ) : (
                  <Wrench size={14} aria-hidden />
                )}
                {busy ? "Writing a patch" : "Ask the Fix Advisor"}
              </button>
              <p className="mt-2 text-[10px] leading-relaxed text-[#4d4d4d]">
                {isDemo
                  ? "Unavailable for a stored analysis: the patch is checked against the clone, which is not stored."
                  : "Writes a unified diff and checks it applies to the clone before showing it."}
              </p>
            </>
          )}
          {fixError && (
            <p role="alert" className="mt-2 text-[11px] text-amber-300">
              {fixError}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function FixView({ fix }: { fix: ProposedFix }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(fix.diff);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard access can be denied; the diff is on screen either way.
    }
  };

  return (
    <div className="space-y-3">
      <p
        className={`flex items-center gap-2 text-[11px] font-bold ${
          fix.applies ? "text-[#00ff41]" : "text-amber-300"
        }`}
      >
        {fix.applies ? <Check size={13} aria-hidden /> : <AlertTriangle size={13} aria-hidden />}
        {fix.applies ? "Applies cleanly to the analysed commit" : "Does not apply"}
      </p>
      <p className="text-[11px] text-[#5a5a5a]">{fix.validation}</p>

      <pre className="max-h-72 overflow-auto rounded border border-white/10 bg-[#050505] p-3 font-mono text-[11px] leading-relaxed text-[#8a8a8a]">
        {fix.diff}
      </pre>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void copy()}
          className="inline-flex items-center gap-1.5 rounded border border-white/10 px-2.5 py-1.5 text-[11px] text-[#8a8a8a] hover:text-[#00ff41]"
        >
          {copied ? <Check size={12} aria-hidden /> : <FileCode size={12} aria-hidden />}
          {copied ? "Copied" : "Copy diff"}
        </button>
        <span className="font-mono text-[10px] text-[#4d4d4d]">{fix.model}</span>
      </div>

      {fix.explanation && <p className="text-[#8a8a8a]">{fix.explanation}</p>}

      {fix.risk && fix.risk !== "low" && (
        <div className="rounded border border-amber-500/20 bg-amber-500/5 p-3 text-[11px]">
          <p className="font-bold text-amber-200">Risk: {fix.risk}</p>
          {(fix.risk_notes || []).map((note, index) => (
            <p key={index} className="mt-1 text-[#a0a0a0]">
              {note}
            </p>
          ))}
        </div>
      )}

      {(fix.alternatives || []).length > 0 && (
        <details className="rounded border border-white/10 p-3 text-[11px]">
          <summary className="cursor-pointer text-[#8a8a8a]">
            {fix.alternatives.length} alternative approach
            {fix.alternatives.length === 1 ? "" : "es"} considered
          </summary>
          <ul className="mt-2 space-y-1 text-[#6a6a6a]">
            {fix.alternatives.map((alternative, index) => (
              <li key={index}>- {alternative}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function HotspotsPanel({ results }: { results: AnalysisResult }) {
  const [open, setOpen] = useState<string | null>(null);
  const hotspots = results.hotspots || [];

  return (
    <Panel
      title={`Hotspots · ${hotspots.length}`}
      icon={<Flame size={14} className="text-orange-400" aria-hidden />}
    >
      {hotspots.length === 0 ? (
        <p className="p-6 text-center text-xs text-[#5a5a5a]">
          Nothing scored high enough to be a hotspot in this repository.
        </p>
      ) : (
        <ol className="divide-y divide-white/5">
          {hotspots.map((hotspot, index) => {
            const isOpen = open === hotspot.file_path;
            return (
              <li key={hotspot.file_path}>
                <button
                  type="button"
                  onClick={() => setOpen(isOpen ? null : hotspot.file_path)}
                  aria-expanded={isOpen}
                  className="flex w-full items-start justify-between gap-3 p-4 text-left transition-colors hover:bg-white/[0.02]"
                >
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 text-sm font-semibold text-[#e5e5e5]">
                      <span className="shrink-0 font-mono text-xs text-[#00ff41]">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                      <span className="truncate">{hotspot.file_path}</span>
                    </p>
                    <p className="mt-1 flex flex-wrap gap-x-3 font-mono text-[10px] text-[#5a5a5a]">
                      <span>complexity {hotspot.complexity}</span>
                      <span>fan-in {hotspot.fan_in}</span>
                      <span>fan-out {hotspot.fan_out}</span>
                      <span>{hotspot.commits} commits</span>
                    </p>
                  </div>
                  <ChevronDown
                    size={15}
                    className={`mt-1 shrink-0 text-[#4d4d4d] transition-transform ${
                      isOpen ? "rotate-180" : ""
                    }`}
                    aria-hidden
                  />
                </button>
                {isOpen && (
                  <div className="space-y-3 bg-[#0a0a0a] px-4 pb-4 pl-10 text-[11px]">
                    <ul className="space-y-1 text-[#8a8a8a]">
                      {(hotspot.reasons || []).map((reason, reasonIndex) => (
                        <li key={reasonIndex}>- {reason}</li>
                      ))}
                    </ul>
                    {hotspot.functions?.length > 0 && (
                      <div>
                        <p className="text-[10px] uppercase tracking-widest text-[#4d4d4d]">
                          most complex functions
                        </p>
                        <ul className="mt-1 space-y-0.5 font-mono text-[10px] text-[#6a6a6a]">
                          {hotspot.functions.slice(0, 5).map((fn, fnIndex) => (
                            <li key={`${fn.name}-${fnIndex}`} className="truncate">
                              {fn.name} · complexity {fn.complexity} · line {fn.start_line}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </Panel>
  );
}

function DependenciesPanel({
  results,
  compact = false,
}: {
  results: AnalysisResult;
  compact?: boolean;
}) {
  const manifests = results.dependency_manifests || [];
  const rows = useMemo(() => {
    const seen = new Set<string>();
    const list: Array<{
      key: string;
      name: string;
      version: string;
      ecosystem: string;
      declared_in: string;
      severity?: string;
      sources?: { title: string; url: string }[];
      reason?: string;
    }> = [];

    for (const manifest of manifests) {
      for (const dependency of manifest.dependencies || []) {
        const name = String(dependency.name || "");
        if (!name || seen.has(name)) continue;
        seen.add(name);
        list.push({
          key: `${manifest.file_path}:${name}`,
          name,
          version: String(dependency.version || dependency.declared_spec || ""),
          ecosystem: manifest.ecosystem || dependency.ecosystem || "",
          declared_in: manifest.file_path,
          severity: dependency.severity,
          sources: dependency.sources,
          reason: dependency.reason || dependency.summary,
        });
      }
    }

    const vulnerable = list.filter((row) => row.severity);
    return [...vulnerable, ...list.filter((row) => !row.severity)].sort((a, b) =>
      a.name.localeCompare(b.name)
    );
  }, [manifests]);

  const researched = results.dependency_research || {};
  const notes = Object.entries(researched).filter(
    ([, value]) => value && typeof value === "object"
  );

  return (
    <Panel
      title={`Dependencies · ${rows.length}`}
      icon={<Package size={14} className="text-sky-400" aria-hidden />}
      actions={
        manifests.length > 0 ? (
          <span className="hidden text-[10px] text-[#4d4d4d] sm:inline">
            {manifests.map((m) => m.file_path).join(", ")}
          </span>
        ) : null
      }
    >
      {rows.length === 0 ? (
        <p className="p-6 text-center text-xs text-[#5a5a5a]">
          No dependency manifests were found in this repository.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[420px] text-left text-[11px]">
            <thead className="border-b border-white/10 text-[10px] uppercase tracking-widest text-[#4d4d4d]">
              <tr>
                <th scope="col" className="px-4 py-2 font-normal">Package</th>
                <th scope="col" className="px-4 py-2 font-normal">Version</th>
                <th scope="col" className="px-4 py-2 font-normal">Ecosystem</th>
                {!compact && <th scope="col" className="px-4 py-2 font-normal">Declared in</th>}
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {rows.slice(0, compact ? 8 : rows.length).map((row) => (
                <tr key={row.key} className="align-top">
                  <td className="px-4 py-2">
                    <span className="font-semibold text-[#e5e5e5]">{row.name}</span>
                    {row.severity && (
                      <span className="ml-2 rounded bg-red-500/15 px-1.5 py-0.5 text-[9px] font-bold uppercase text-red-300">
                        {row.severity}
                      </span>
                    )}
                    {row.sources?.length ? (
                      <span className="ml-2 inline-flex items-center gap-1 text-[9px] text-sky-400">
                        <Globe size={9} aria-hidden /> sourced
                      </span>
                    ) : null}
                  </td>
                  <td className="px-4 py-2 font-mono text-[#8a8a8a]">{row.version || "-"}</td>
                  <td className="px-4 py-2 text-[#6a6a6a]">{row.ecosystem || "-"}</td>
                  {!compact && (
                    <td className="px-4 py-2 font-mono text-[#4d4d4d]">{row.declared_in}</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {notes.length > 0 && (
        <div className="space-y-3 border-t border-white/10 p-4">
          <p className="text-[10px] uppercase tracking-widest text-[#4d4d4d]">
            Researched verdicts
          </p>
          {notes.slice(0, compact ? 2 : 6).map(([name, value]) => {
            const record = value as Record<string, unknown>;
            return (
              <div key={name} className="text-[11px]">
                <p className="font-semibold text-[#e5e5e5]">{name}</p>
                {typeof record.reason === "string" && (
                  <p className="mt-0.5 text-[#8a8a8a]">{record.reason}</p>
                )}
                <SourceLinks sources={(record.sources as any[]) || []} />
              </div>
            );
          })}
        </div>
      )}
    </Panel>
  );
}

function ArchitecturePanel({ results }: { results: AnalysisResult }) {
  const architecture = results.architecture || {};
  const stack = results.tech_stack || {};
  const stackEntries = Object.entries(stack).filter(
    ([, value]) => Array.isArray(value) && value.length > 0
  );

  return (
    <Panel
      title="Architecture"
      icon={<Cpu size={14} className="text-purple-400" aria-hidden />}
      actions={
        architecture.generated_by ? (
          <span className="hidden max-w-[160px] truncate font-mono text-[10px] text-[#4d4d4d] sm:inline">
            {architecture.generated_by}
          </span>
        ) : null
      }
    >
      <div className="space-y-4 p-4 text-xs leading-relaxed">
        {architecture.summary ? (
          <p className="text-[#a0a0a0]">{architecture.summary}</p>
        ) : architecture.generated_by ? (
          <p className="text-[#5a5a5a]">
            {architecture.generated_by} was asked for this and its answer did not contain
            anything usable, so nothing is shown rather than something invented.
          </p>
        ) : (
          <p className="text-[#5a5a5a]">
            No architecture summary in this analysis. It is written by the large model, so an
            instance without model access produces the measurements but not the narrative.
          </p>
        )}

        {architecture.pattern && (
          <p>
            <span className="text-[#4d4d4d]">Pattern: </span>
            <span className="text-[#8a8a8a]">{architecture.pattern}</span>
          </p>
        )}

        {(architecture.layers || []).length > 0 && (
          <div>
            <p className="text-[10px] uppercase tracking-widest text-[#4d4d4d]">Layers</p>
            <ul className="mt-1 space-y-1">
              {(architecture.layers || []).map((layer, index) => (
                <li key={`${layer.name}-${index}`} className="text-[#8a8a8a]">
                  <span className="font-semibold text-[#e5e5e5]">{layer.name}</span>
                  {layer.responsibility && <span> - {layer.responsibility}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {(architecture.risks || []).length > 0 && (
          <div>
            <p className="text-[10px] uppercase tracking-widest text-[#4d4d4d]">
              Risks called out
            </p>
            <ul className="mt-1 space-y-1">
              {(architecture.risks || []).map((risk, index) => (
                <li key={index} className="text-[#8a8a8a]">
                  {risk.file_path && <span className="font-mono text-[#5a5a5a]">{risk.file_path}: </span>}
                  {risk.risk}
                </li>
              ))}
            </ul>
          </div>
        )}

        {stackEntries.length > 0 && (
          <div className="border-t border-white/5 pt-3">
            {stackEntries.map(([category, items]) => (
              <div key={category} className="mb-2 last:mb-0">
                <p className="text-[10px] uppercase tracking-widest text-[#4d4d4d]">
                  {category.replace(/_/g, " ")}
                </p>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {(items as string[]).map((item, index) => (
                    <span
                      key={`${item}-${index}`}
                      className="rounded border border-white/10 px-1.5 py-0.5 text-[10px] text-[#8a8a8a]"
                    >
                      {item}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Panel>
  );
}

function ChatPanel({
  results,
  getIdToken,
  isDemo,
}: {
  results: AnalysisResult;
  getIdToken: () => Promise<string | null>;
  isDemo: boolean;
}) {
  return (
    <Panel
      title="Ask the repository"
      icon={<Bot size={14} className="text-[#00ff41]" aria-hidden />}
      actions={<span className="hidden text-[10px] text-[#4d4d4d] sm:inline">fast model · tools</span>}
    >
      <div className="h-[420px]">
        <AIChat repoId={results.repo_id} getIdToken={getIdToken} disabled={isDemo} />
      </div>
    </Panel>
  );
}

// --------------------------------------------------------------------------- //
// shared bits
// --------------------------------------------------------------------------- //

function SeverityBadge({ severity, count }: { severity: string; count?: number }) {
  const tones: Record<string, string> = {
    CRITICAL: "border-red-500/40 bg-red-500/10 text-red-300",
    HIGH: "border-orange-500/40 bg-orange-500/10 text-orange-300",
    MEDIUM: "border-yellow-500/40 bg-yellow-500/10 text-yellow-300",
    LOW: "border-blue-500/40 bg-blue-500/10 text-blue-300",
  };
  const tone = tones[severity] || "border-white/10 bg-white/5 text-[#8a8a8a]";
  return (
    <span
      className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-wide ${tone}`}
    >
      {severity}
      {count !== undefined ? ` ${count}` : ""}
    </span>
  );
}

function SourceLinks({ sources }: { sources: { title: string; url: string }[] }) {
  if (!sources.length) return null;
  return (
    <ul className="mt-2 space-y-1">
      {sources.slice(0, 4).map((source, index) => (
        <li key={`${source.url}-${index}`}>
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-start gap-1.5 text-[10px] text-sky-400 underline decoration-dotted underline-offset-2 hover:text-sky-300"
          >
            <Globe size={10} className="mt-0.5 shrink-0" aria-hidden />
            <span className="break-all">{source.title || source.url}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

export { Search, Terminal };