"use client";

import { useState, useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Code2,
  Cpu,
  Flame,
  GitBranch,
  Loader2,
  Package,
  ShieldAlert,
  LogOut,
  ArrowLeft,
  X,
  FileCode,
} from "lucide-react";
import GraphView from "@/components/GraphView";
import AIChat from "@/components/AIChat";
import { useAuth } from "@/hooks/useAuth";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

type Vulnerability = {
  id: string;
  name: string;
  severity: "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | string;
  description: string;
  file_path: string;
  line: number;
  snippet: string;
};

type AnalyzedFile = {
  file_path: string;
  language: string;
  functions: Array<{ name: string }>;
  classes: Array<{ name: string }>;
  complexity: number;
  vulnerabilities?: Vulnerability[];
};

export default function ResultsPage() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [results, setResults] = useState<any>(null);
  const [repoUrl, setRepoUrl] = useState("");
  const [displayedHotspots, setDisplayedHotspots] = useState(5);
  const [displayedFindings, setDisplayedFindings] = useState(5);
  const [displayedDependencies, setDisplayedDependencies] = useState(8);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [previewFile, setPreviewFile] = useState<{ path: string; language: string; content: string } | null>(null);

  useEffect(() => {
    const resultsStr = sessionStorage.getItem("analysisResults");
    const repoUrlStr = sessionStorage.getItem("repoUrl");

    if (!resultsStr) {
      router.push("/dashboard");
      return;
    }

    try {
      setResults(JSON.parse(resultsStr));
      setRepoUrl(repoUrlStr || "");
    } catch (e) {
      router.push("/dashboard");
    }
  }, [router]);

  const allDependencies = useMemo(() => {
    // Build dependencies list from both dependency_manifests and ai_dependencies if available
    const vulnerableMap = new Map();
    const outdatedMap = new Map();
    const deps = new Map<string, any>();

    if (results?.ai_dependencies) {
      results.ai_dependencies.vulnerable?.forEach((d: any) => {
        if (!d || !d.name) return;
        vulnerableMap.set(d.name, d);
        if (!deps.has(d.name)) {
          deps.set(d.name, {
            name: d.name,
            version: d.version || d.current || "",
            isVulnerable: true,
            isOutdated: false,
            vulnerabilityInfo: d,
            outdatedInfo: null,
          });
        } else {
          const e = deps.get(d.name);
          e.isVulnerable = true;
          e.vulnerabilityInfo = d;
        }
      });

      results.ai_dependencies.outdated?.forEach((d: any) => {
        if (!d || !d.name) return;
        outdatedMap.set(d.name, d);
        if (!deps.has(d.name)) {
          deps.set(d.name, {
            name: d.name,
            version: d.current || d.version || "",
            isVulnerable: false,
            isOutdated: true,
            vulnerabilityInfo: null,
            outdatedInfo: d,
          });
        } else {
          const e = deps.get(d.name);
          e.isOutdated = true;
          e.outdatedInfo = d;
        }
      });
    }

    if (results?.dependency_manifests) {
      results.dependency_manifests.forEach((manifest: any) => {
        manifest.dependencies?.forEach((dep: any) => {
          if (!dep || !dep.name) return;
          if (!deps.has(dep.name)) {
            deps.set(dep.name, {
              name: dep.name,
              version: dep.version || "",
              isVulnerable: vulnerableMap.has(dep.name),
              isOutdated: outdatedMap.has(dep.name),
              vulnerabilityInfo: vulnerableMap.get(dep.name),
              outdatedInfo: outdatedMap.get(dep.name),
            });
          } else {
            const e = deps.get(dep.name);
            e.version = e.version || dep.version || "";
          }
        });
      });
    }

    if (deps.size === 0) return [];

    // Convert to array and sort (vulnerable first, then outdated, then alphabetical)
    return Array.from(deps.values()).sort((a, b) => {
      if (a.isVulnerable && !b.isVulnerable) return -1;
      if (!a.isVulnerable && b.isVulnerable) return 1;
      if (a.isOutdated && !b.isOutdated) return -1;
      if (!a.isOutdated && b.isOutdated) return 1;
      return a.name.localeCompare(b.name);
    });
  }, [results]);

  if (!user) {
    return (
      <div className="min-h-screen bg-[#050505] flex items-center justify-center">
        <Loader2 className="animate-spin text-[#00ff41]" size={32} />
      </div>
    );
  }

  if (!results) {
    return (
      <div className="min-h-screen bg-[#050505] flex items-center justify-center">
        <Loader2 className="animate-spin text-[#00ff41]" size={32} />
      </div>
    );
  }

  const handleLogout = async () => {
    await logout();
    router.push("/");
  };

  const handleNewAnalysis = () => {
    sessionStorage.removeItem("analysisResults");
    sessionStorage.removeItem("repoUrl");
    router.push("/dashboard");
  };

  const handleGraphNodeClick = async (node: any) => {
    const filePath = String(node?.path || node?.id || "");
    if (!filePath || node?.isCentral) return;

    try {
      setPreviewOpen(true);
      setPreviewLoading(true);
      setPreviewError("");
      setPreviewFile(null);

      const token = await user.getIdToken();
      const response = await fetch(`${API_BASE_URL}/repo/file`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
        },
        body: JSON.stringify({ repo_id: results.repo_id, file_path: filePath }),
      });

      const data = await response.json();
      if (!response.ok || data.error) {
        throw new Error(data.error || "Could not load file contents.");
      }

      setPreviewFile({
        path: data.file_path || filePath,
        language: data.language || String(node?.language || "text"),
        content: data.content || "",
      });
    } catch (error: any) {
      setPreviewError(error?.message || "Could not load file contents.");
    } finally {
      setPreviewLoading(false);
    }
  };

  const files: AnalyzedFile[] = results?.files || [];
  const vulnerabilities: Vulnerability[] = results?.vulnerabilities || [];
  const hotspots: AnalyzedFile[] = results?.hotspots || [];
  const graphLinks = results?.stats?.graph_links ?? results?.graph?.links?.length ?? 0;
  const criticalHigh = (results?.stats?.critical_vulnerabilities || 0) + (results?.stats?.high_vulnerabilities || 0);

  return (
    <main className="min-h-screen bg-[#050505] text-[#f0f0f0]">
      {/* Header */}
      <header className="border-b border-[#222] px-6 py-4 flex justify-between items-center">
        <div>
          <div className="flex items-center gap-2 mb-2">
            <Code2 className="text-[#00ff41]" size={24} />
            <h1 className="text-2xl font-bold tracking-tighter">
              CODELENS <span className="text-xs bg-[#003b11] text-[#00ff41] px-2 py-0.5 rounded ml-2">BETA</span>
            </h1>
          </div>
          {repoUrl && <p className="text-[#888] text-xs font-mono">{repoUrl}</p>}
        </div>
        <div className="flex gap-3">
          <button
            onClick={handleNewAnalysis}
            className="flex items-center gap-2 text-xs border border-[#222] px-4 py-2 rounded hover:bg-[#111] transition-all"
          >
            <ArrowLeft size={14} />
            New Analysis
          </button>
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 text-xs border border-red-900/30 text-red-500 bg-red-500/5 px-4 py-2 rounded hover:bg-red-500/10 transition-all"
          >
            <LogOut size={14} />
            Logout
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div className="p-8 grid grid-cols-[320px_1fr_340px] gap-8 min-h-[calc(100vh-90px)]">
        {/* Left Sidebar - Stats */}
        <div className="space-y-4 overflow-y-auto pr-2">
          <h2 className="text-xs font-bold text-[#666] uppercase tracking-widest px-3 mb-6">Overview</h2>

          <StatCard
            icon={<Code2 size={18} />}
            label="Files"
            value={results.stats.total_files}
            subLabel="analyzed"
          />
          <StatCard
            icon={<Package size={18} />}
            label="Dependencies"
            value={results.stats.dependencies || 0}
            subLabel="total"
          />
          <StatCard
            icon={<GitBranch size={18} />}
            label="Graph Edges"
            value={graphLinks}
            subLabel="connections"
          />
          <StatCard
            icon={<ShieldAlert size={18} />}
            label="Findings"
            value={results.stats.total_vulnerabilities}
            subLabel={`${criticalHigh} critical/high`}
            tone={criticalHigh ? "danger" : "default"}
          />
          <StatCard
            icon={<Flame size={18} />}
            label="Hotspots"
            value={results.stats.hotspot_count}
            subLabel="complex areas"
          />
          <StatCard
            icon={<Cpu size={18} />}
            label="Languages"
            value={Object.keys(results.stats.languages).length}
            subLabel="used"
          />
          {results?.tech_stack?.frameworks && results.tech_stack.frameworks.length > 0 && (
            <StatCard
              icon={<Cpu size={18} />}
              label="Frameworks"
              value={results.tech_stack.frameworks.length}
              subLabel="identified"
              tone="purple"
            />
          )}
          {results?.ai_dependencies?.vulnerable && results.ai_dependencies.vulnerable.length > 0 && (
            <StatCard
              icon={<ShieldAlert size={18} />}
              label="Vuln. Packages"
              value={results.ai_dependencies.vulnerable.length}
              subLabel="critical/high"
              tone="danger"
            />
          )}
          {results?.ai_dependencies?.outdated && results.ai_dependencies.outdated.length > 0 && (
            <StatCard
              icon={<Package size={18} />}
              label="Outdated"
              value={results.ai_dependencies.outdated.length}
              subLabel="packages"
              tone="warning"
            />
          )}

          {/* Files tree inside left sidebar */}
          <div>
            <h2 className="text-xs font-bold text-[#666] uppercase tracking-widest px-3 mt-2 mb-4">Files</h2>
            <div className="bg-[#0a0a0a] border border-[#222] rounded-xl p-3 max-h-[520px] overflow-auto">
              {files.length > 0 ? (
                <div className="text-[13px] text-[#e5e5e5]">
                  <FileTree files={files} />
                </div>
              ) : (
                <p className="text-[11px] text-[#555] text-center py-6">No files available</p>
              )}
            </div>
          </div>
        </div>

        {/* Center - Graph & Chat */}
        <div className="flex flex-col gap-4 h-full min-w-0 min-h-0">
          {/* Graph */}
          <div className="bg-[#0a0a0a] border border-[#222] rounded-xl overflow-hidden shadow-lg shrink-0">
            <div className="bg-[#111] border-b border-[#222] px-5 py-3.5">
              <h3 className="text-sm font-bold text-[#888] uppercase tracking-widest flex items-center gap-2.5">
                <GitBranch size={15} className="text-[#00ff41]" /> Dependency Graph
              </h3>
            </div>
            <div className="h-[460px] bg-[#0a0a0a]">
              <GraphView data={results.graph} onNodeClick={handleGraphNodeClick} />
            </div>
          </div>

          {/* AI Chat */}
          <div className="bg-[#0a0a0a] border border-[#222] rounded-xl overflow-hidden shadow-lg flex-1 flex flex-col min-h-0">
            <div className="bg-[#111] border-b border-[#222] px-5 py-3.5 shrink-0">
              <h3 className="text-sm font-bold text-[#888] uppercase tracking-widest flex items-center gap-2.5">
                <Code2 size={15} className="text-[#00ff41]" /> AI Assistant
              </h3>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              <AIChat repoId={results.repo_id} user={user} className="h-full" />
            </div>
          </div>
        </div>

        {/* Right Sidebar - Findings, Hotspots, Dependencies, Tech Stack */}
        <div className="space-y-4 overflow-y-auto pr-2">
          <h2 className="text-xs font-bold text-[#666] uppercase tracking-widest px-3 mb-6">Details</h2>

          {/* Security Findings */}
          <div className="bg-[#0a0a0a] border border-red-500/30 rounded-xl overflow-hidden hover:border-red-500/20 transition-all">
            <div className="bg-[#111] border-b border-[#222] px-5 py-3">
              <h3 className="text-sm font-bold text-[#888] uppercase tracking-widest flex items-center gap-2.5">
                <AlertTriangle size={14} className="text-red-500" /> Security Findings
              </h3>
            </div>
            <div className="max-h-[320px] overflow-y-auto">
              {vulnerabilities.length > 0 ? (
                <div className="space-y-3 p-5">
                  {vulnerabilities.slice(0, displayedFindings).map((finding, i) => (
                    <div key={`${finding.file_path}-${finding.line}-${i}`} className="border-l-2 border-red-500/30 pl-4 py-3">
                      <div className="flex items-start justify-between gap-2 mb-2">
                        <p className="text-[12px] font-bold text-[#e5e5e5] truncate flex-1">{finding.name}</p>
                        <SeverityBadge severity={finding.severity} />
                      </div>
                      <p className="text-[10px] text-[#666] font-mono">{finding.file_path}:{finding.line}</p>
                    </div>
                  ))}
                  {vulnerabilities.length > displayedFindings && (
                    <button
                      onClick={() => setDisplayedFindings(displayedFindings + 5)}
                      className="w-full text-[11px] text-red-500 hover:text-red-500/80 text-center py-3 hover:bg-red-500/5 rounded transition-all"
                    >
                      +{vulnerabilities.length - displayedFindings} more
                    </button>
                  )}
                </div>
              ) : (
                <p className="text-[11px] text-[#555] text-center py-6">No security issues found</p>
              )}
            </div>
          </div>

          {/* Hotspots */}
          <div className="bg-[#0a0a0a] border border-orange-500/30 rounded-xl overflow-hidden hover:border-orange-500/20 transition-all">
            <div className="bg-[#111] border-b border-[#222] px-5 py-3">
              <h3 className="text-sm font-bold text-[#888] uppercase tracking-widest flex items-center gap-2.5">
                <Flame size={14} className="text-orange-500" /> Hotspots
              </h3>
            </div>
            <div className="max-h-[320px] overflow-y-auto">
              {hotspots.length > 0 ? (
                <div className="space-y-3 p-5">
                  {hotspots.slice(0, displayedHotspots).map((file, idx) => (
                    <div key={file.file_path} className="border-l-2 border-orange-500/30 pl-4 py-3">
                      <div className="flex items-start justify-between gap-2 mb-2">
                        <p className="text-[13px] text-[#e5e5e5] font-bold flex-1">{file.file_path.split('/').pop()}</p>
                        <span className="text-[12px] bg-[#00ff41]/20 text-[#00ff41] px-2 py-0.5 rounded font-bold shrink-0">#{idx + 1}</span>
                      </div>
                      <p className="text-[11px] text-[#666] text-center">{file.file_path}</p>
                    </div>
                  ))}
                  {hotspots.length > displayedHotspots && (
                    <button
                      onClick={() => setDisplayedHotspots(displayedHotspots + 5)}
                      className="w-full text-[11px] text-[#00ff41] hover:text-[#00ff41]/80 text-center py-3 hover:bg-[#00ff41]/5 rounded transition-all"
                    >
                      +{hotspots.length - displayedHotspots} more
                    </button>
                  )}
                </div>
              ) : (
                <p className="text-[11px] text-[#555] text-center py-6">No hotspots found</p>
              )}
            </div>
          </div>

          {/* Dependencies */}
          <div className="bg-[#0a0a0a] border border-blue-500/30 rounded-xl overflow-hidden hover:border-blue-500/20 transition-all">
            <div className="bg-[#111] border-b border-[#222] px-5 py-3">
              <h3 className="text-sm font-bold text-[#888] uppercase tracking-widest flex items-center gap-2.5">
                <Package size={14} className="text-blue-500" /> Dependencies ({allDependencies.length})
              </h3>
            </div>
            <div className="max-h-[320px] overflow-y-auto">
              {allDependencies.length > 0 ? (
                <div className="space-y-1 p-5">
                  {allDependencies.slice(0, displayedDependencies).map((dep: any, i: number) => (
                    <div key={`dep-${i}`} className="flex items-center justify-between gap-3 py-2 border-b border-[#111] last:border-0">
                      <div className="flex flex-col min-w-0">
                        <span className="text-[11px] text-[#e5e5e5] truncate font-medium flex items-center gap-2">
                          {dep.name}
                          {dep.isVulnerable && <span className="w-1.5 h-1.5 rounded-full bg-red-500" title="Vulnerable"></span>}
                          {dep.isOutdated && !dep.isVulnerable && <span className="w-1.5 h-1.5 rounded-full bg-yellow-500" title="Outdated"></span>}
                        </span>
                        {(dep.isVulnerable || dep.isOutdated) && (
                          <span className="text-[9px] text-[#888] mt-0.5 truncate">
                            {dep.isVulnerable ? dep.vulnerabilityInfo?.severity || dep.vulnerabilityInfo?.vulnerability : `${dep.outdatedInfo?.current || dep.version} → ${dep.outdatedInfo?.latest}`}
                          </span>
                        )}
                      </div>
                      <span className={`px-2 py-1 rounded shrink-0 font-mono text-[9px] font-bold ${dep.isVulnerable ? 'text-red-400 bg-red-500/10' : dep.isOutdated ? 'text-yellow-400 bg-yellow-500/10' : 'text-[#666] bg-[#111]'}`}>
                        {dep.version}
                      </span>
                    </div>
                  ))}
                  {allDependencies.length > displayedDependencies && (
                    <button
                      onClick={() => setDisplayedDependencies(displayedDependencies + 8)}
                      className="w-full text-[11px] text-blue-500 hover:text-blue-500/80 text-center py-3 hover:bg-blue-500/5 rounded transition-all"
                    >
                      +{allDependencies.length - displayedDependencies} more
                    </button>
                  )}
                </div>
              ) : (
                <p className="text-[11px] text-[#555] text-center py-6">No dependencies found</p>
              )}
            </div>
          </div>

          {/* Tech Stack */}
          <div className="bg-[#0a0a0a] border border-purple-500/30 rounded-xl overflow-hidden hover:border-purple-500/20 transition-all">
            <div className="bg-[#111] border-b border-[#222] px-5 py-3">
              <h3 className="text-sm font-bold text-[#888] uppercase tracking-widest flex items-center gap-2.5">
                <Cpu size={14} className="text-purple-500" /> Tech Stack
              </h3>
            </div>
            <div className="max-h-[320px] overflow-y-auto">
              {results?.tech_stack && Object.keys(results.tech_stack).length > 0 ? (
                <div className="space-y-4 p-5">
                  {Object.entries(results.tech_stack).map(([category, items]: [string, any]) => {
                    if (!Array.isArray(items) || items.length === 0) return null;
                    return (
                      <div key={category}>
                        <div className="text-[10px] text-[#888] font-bold uppercase tracking-widest mb-2">{category.replace('_', ' ')}</div>
                        <div className="flex flex-wrap gap-2">
                          {items.map((item: string, idx: number) => (
                            <span key={`${item}-${idx}`} className="text-[#e5e5e5] bg-[#111] border border-[#222] px-2 py-1 rounded text-[10px] font-medium">
                              {item}
                            </span>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-[11px] text-[#555] text-center py-6">No tech stack data</p>
              )}
            </div>
          </div>
        </div>
      </div>

      {previewOpen && (
        <div className="fixed inset-0 z-[60] bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-5xl h-[80vh] bg-[#0a0a0a] border border-[#222] rounded-2xl shadow-2xl overflow-hidden flex flex-col">
            <div className="px-4 py-3 border-b border-[#222] flex items-center justify-between bg-[#111]">
              <div className="min-w-0">
                <div className="flex items-center gap-2 text-[#00ff41] text-sm font-bold">
                  <FileCode size={14} /> File Preview
                </div>
                <p className="text-[11px] text-[#888] font-mono truncate">
                  {previewFile?.path || "Loading file..."}
                </p>
              </div>
              <button
                onClick={() => setPreviewOpen(false)}
                className="w-9 h-9 rounded-lg border border-[#222] bg-[#0a0a0a] text-[#888] hover:text-white hover:border-[#00ff41]/50 transition-all flex items-center justify-center"
                aria-label="Close file preview"
              >
                <X size={16} />
              </button>
            </div>

            <div className="flex-1 min-h-0 overflow-hidden">
              {previewLoading ? (
                <div className="h-full flex items-center justify-center text-[#00ff41] gap-3">
                  <Loader2 className="animate-spin" size={20} />
                  Loading file contents...
                </div>
              ) : previewError ? (
                <div className="h-full flex items-center justify-center p-6 text-center text-red-400">
                  <div>
                    <p className="font-bold mb-2">Could not open file</p>
                    <p className="text-sm text-red-300/80">{previewError}</p>
                  </div>
                </div>
              ) : (
                <div className="h-full flex flex-col">
                  <div className="px-4 py-2 border-b border-[#222] text-[11px] text-[#666] font-mono bg-[#050505] flex items-center justify-between gap-3">
                    <span>{previewFile?.language || "text"}</span>
                    <span>{previewFile?.content?.length || 0} chars</span>
                  </div>
                  <pre className="flex-1 min-h-0 overflow-auto p-4 text-[12px] leading-6 font-mono text-[#e5e5e5] whitespace-pre break-words bg-[#050505]">
                    <code>{previewFile?.content || "No file contents available."}</code>
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  );
}

function StatCard({ icon, label, value, subLabel, tone = "default" }: any) {
  const isDanger = tone === "danger";
  const isWarning = tone === "warning";
  const isPurple = tone === "purple";

  let borderColor = "border-[#222]";
  let iconBg = "bg-[#00ff41]/10 text-[#00ff41]";
  let hoverBorder = "hover:border-[#00ff41]/30";

  if (isDanger) {
    borderColor = "border-red-500/30";
    iconBg = "bg-red-500/10 text-red-400";
    hoverBorder = "hover:border-red-500/50";
  } else if (isWarning) {
    borderColor = "border-yellow-500/30";
    iconBg = "bg-yellow-500/10 text-yellow-400";
    hoverBorder = "hover:border-yellow-500/50";
  } else if (isPurple) {
    borderColor = "border-purple-500/30";
    iconBg = "bg-purple-500/10 text-purple-400";
    hoverBorder = "hover:border-purple-500/50";
  }

  return (
    <div className={`bg-[#0a0a0a] border rounded-xl p-5 transition-all ${borderColor} ${hoverBorder}`}>
      <div className="flex items-start gap-3.5">
        <div className={`p-3 rounded-lg ${iconBg}`}>
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] text-[#666] uppercase font-bold tracking-wide">{label}</p>
          <p className="text-3xl font-bold mt-2">{value}</p>
          <p className="text-[10px] text-[#555] mt-2">{subLabel}</p>
        </div>
      </div>
    </div>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const classes: Record<string, string> = {
    CRITICAL: "bg-red-500/15 text-red-300 border-red-500/30",
    HIGH: "bg-orange-500/15 text-orange-300 border-orange-500/30",
    MEDIUM: "bg-yellow-500/15 text-yellow-300 border-yellow-500/30",
    LOW: "bg-blue-500/15 text-blue-300 border-blue-500/30",
  };
  return <span className={`text-[8px] border px-1.5 py-0.5 rounded font-bold ${classes[severity] || "bg-[#111] text-[#777] border-[#222]"}`}>{severity}</span>;
}

// --- File tree builder & renderer ---
type TreeNode = {
  name: string;
  path?: string;
  children?: TreeNode[];
  language?: string;
  vulnerabilities?: any[];
  isFile?: boolean;
};

function buildFileTree(files: AnalyzedFile[]): TreeNode[] {
  const rootMap: Map<string, any> = new Map();

  for (const f of files) {
    const parts = f.file_path.split("/");
    let cur = rootMap;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isLast = i === parts.length - 1;
      if (!cur.has(part)) {
        cur.set(part, { _meta: { name: part, children: new Map(), isFile: false } });
      }
      const entry = cur.get(part);
      if (isLast) {
        // mark as file
        entry._meta.isFile = true;
        entry._meta.path = f.file_path;
        entry._meta.language = f.language;
        entry._meta.vulnerabilities = f.vulnerabilities || [];
      }
      cur = entry._meta.children;
    }
  }

  function mapMapToArray(map: Map<string, any>): TreeNode[] {
    const arr: TreeNode[] = [];
    for (const [key, value] of map.entries()) {
      const meta = value._meta;
      const node: TreeNode = {
        name: meta.name,
        path: meta.path,
        language: meta.language,
        vulnerabilities: meta.vulnerabilities,
        isFile: meta.isFile,
      };
      const childrenArr = mapMapToArray(meta.children);
      if (childrenArr.length) node.children = childrenArr.sort((a, b) => (a.isFile === b.isFile ? a.name.localeCompare(b.name) : a.isFile ? 1 : -1));
      arr.push(node);
    }
    // sort folders first then files
    return arr.sort((a, b) => {
      if ((a.isFile ? 1 : 0) !== (b.isFile ? 1 : 0)) return a.isFile ? 1 : -1;
      return a.name.localeCompare(b.name);
    });
  }

  return mapMapToArray(rootMap);
}

function langColor(language?: string, hasVulns?: boolean) {
  if (hasVulns) return "#ef4444";
  switch ((language || "").toLowerCase()) {
    case "python": return "#3776ab";
    case "javascript": return "#f7df1e";
    case "typescript": return "#3178c6";
    case "framework": return "#a855f7";
    case "database": return "#eab308";
    case "package": return "#3b82f6";
    default: return "#00ff41";
  }
}

function FileTree({ files }: { files: AnalyzedFile[] }) {
  const tree = buildFileTree(files);

  return (
    <div className="space-y-1 text-sm">
      {tree.map((node) => (
        <TreeNodeView key={node.name + (node.path || "")} node={node} depth={0} />
      ))}
    </div>
  );
}

function TreeNodeView({ node, depth }: { node: TreeNode; depth: number }) {
  const [open, setOpen] = useState(true);
  const hasChildren = node.children && node.children.length > 0;
  const isFile = !!node.isFile;
  const vulnCount = (node.vulnerabilities || []).length;
  const color = langColor(node.language, vulnCount > 0);

  return (
    <div>
      <div className="flex items-center gap-2 cursor-default" style={{ paddingLeft: depth * 12 }}>
        {hasChildren ? (
          <button onClick={() => setOpen(!open)} className="text-[11px] text-[#888] w-4 h-4 flex items-center justify-center">{open ? '▾' : '▸'}</button>
        ) : <div style={{ width: 16 }} />}

        <div className="w-2 h-2 rounded-full" style={{ backgroundColor: color }} />

        <div className={`truncate ${isFile ? 'text-[13px]' : 'text-[12px] text-[#cfcfcf] font-medium'}`}>
          {node.name}{isFile && node.path ? <span className="text-[11px] text-[#666] font-mono ml-2">{node.path.split('/').pop()}</span> : null}
        </div>
        {isFile && vulnCount > 0 && <span className="ml-auto text-[11px] text-red-400 font-bold">{vulnCount}</span>}
      </div>
      {hasChildren && open && (
        <div className="mt-1">
          {node.children!.map((c) => (
            <TreeNodeView key={(c.path || c.name)} node={c} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}
