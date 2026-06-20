"use client";

import { useState, useCallback, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks/useAuth";
import { GlowingInput } from "@/components/ui/glowing-input";
import { Code2, LogOut, Loader2 } from "lucide-react";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export default function Dashboard() {
  const { user, logout, loading: authLoading } = useAuth();
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [status, setStatus] = useState<any>(null);

  useEffect(() => {
    if (!authLoading && !user) {
      router.push("/");
    }
  }, [user, authLoading, router]);

  const analyzeRepo = useCallback(async (repoUrl: string) => {
    if (!repoUrl.trim() || !user) return;

    setIsAnalyzing(true);
    setLogs([`$ Starting analysis for: ${repoUrl}`]);
    setStatus(null);

    try {
      const token = await user.getIdToken();
      const streamUrl = `${API_BASE_URL}/analyze/stream?url=${encodeURIComponent(repoUrl.trim())}&token=${encodeURIComponent(token)}`;

      const eventSource = new EventSource(streamUrl);

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          setStatus(data);

          if (data.message) {
            setLogs((prev) => [...prev, `$ ${data.message}`]);
          }

          if (data.status === "completed") {
            setLogs((prev) => [...prev, "$ Analysis completed!"]);
            sessionStorage.setItem("analysisResults", JSON.stringify(data.results));
            sessionStorage.setItem("repoUrl", repoUrl);

            // Wait a moment before redirecting
            setTimeout(() => {
              setIsAnalyzing(false);
              router.push("/results");
            }, 1500);

            eventSource.close();
          } else if (data.status === "error") {
            setLogs((prev) => [...prev, `$ ❌ ERROR: ${data.message || "Unknown error"}`]);
            setIsAnalyzing(false);
            eventSource.close();
          }
        } catch (e) {
          setLogs((prev) => [...prev, `$ Parse error: ${e}`]);
        }
      };

      eventSource.onerror = () => {
        setLogs((prev) => [...prev, "$ ❌ Connection error"]);
        setIsAnalyzing(false);
        eventSource.close();
      };
    } catch (error) {
      setLogs((prev) => [...prev, `$ ❌ ERROR: ${error instanceof Error ? error.message : String(error)}`]);
      setIsAnalyzing(false);
    }
  }, [user, router]);

  const handleSubmit = (value: string) => {
    if (value.trim()) {
      analyzeRepo(value.trim());
    }
  };

  const handleLogout = async () => {
    await logout();
    router.push("/");
  };

  if (authLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#050505]">
        <Loader2 className="animate-spin text-[#00ff41]" size={32} />
      </div>
    );
  }

  if (!user) {
    return null;
  }

  return (
    <main className="min-h-screen bg-[#050505] text-[#f0f0f0]">
      {/* Header */}
      <header className="border-b border-[#222] px-6 py-4 flex justify-between items-center">
        <div className="flex items-center gap-2">
          <Code2 className="text-[#00ff41]" size={24} />
          <h1 className="text-2xl font-bold tracking-tighter">
            CODELENS <span className="text-xs bg-[#003b11] text-[#00ff41] px-2 py-0.5 rounded ml-2">BETA</span>
          </h1>
        </div>
        <button
          onClick={handleLogout}
          className="flex items-center gap-2 text-xs border border-red-900/30 text-red-500 bg-red-500/5 px-3 py-2 rounded hover:bg-red-500/10 transition-all"
        >
          <LogOut size={14} />
          Logout
        </button>
      </header>

      {/* Main Content */}
      <div className="min-h-[calc(100vh-80px)] flex items-center justify-center px-4 relative">
        {/* Input Section */}
        {!isAnalyzing && (
          <div className="text-white space-y-8 w-full max-w-2xl animate-in fade-in">
            <div className="text-center space-y-4">
              <h2 className="text-4xl md:text-5xl font-bold tracking-tight">
                Analyze Your Repository
              </h2>
              <p className="text-[#888] text-lg">
                Enter a public GitHub repository URL to get AI-powered insights
              </p>
            </div>

            <GlowingInput
              placeholder="https://github.com/username/repo"
              value={url}
              onChange={setUrl}
              onSubmit={handleSubmit}
              showButton={false}
            />

            <div className="text-center text-[#666] text-sm">
              <p>Logged in as <span className="text-[#888]">{user?.displayName || user?.email}</span></p>
            </div>
          </div>
        )}

        {/* Terminal Overlay */}
        {isAnalyzing && (
          <div className="fixed inset-0 bg-black/90 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div className="w-full max-w-3xl max-h-[80vh] bg-[#0a0a0a] border border-[#00ff41]/30 rounded-lg overflow-hidden flex flex-col shadow-2xl">
              {/* Terminal Header */}
              <div className="bg-[#111] border-b border-[#222] px-4 py-3 flex items-center gap-2">
                <div className="w-3 h-3 rounded-full bg-[#00ff41] animate-pulse"></div>
                <span className="text-[#888] text-sm font-mono">Analysis Terminal</span>
                {status?.status === "analyzing" && (
                  <span className="ml-auto text-xs text-[#00ff41]/60 font-mono animate-pulse">AI ANALYSIS IN PROGRESS</span>
                )}
              </div>

              {/* Terminal Content */}
              <div className="flex-1 overflow-y-auto p-4 font-mono text-sm space-y-1" ref={(el) => { if (el) el.scrollTop = el.scrollHeight; }}>
                {logs.length === 0 ? (
                  <div className="text-[#666]">Initializing...</div>
                ) : (
                  logs.map((log, i) => {
                    // Color-code based on message content
                    let colorClass = "text-[#00ff41]";
                    if (log.includes("❌") || log.includes("ERROR")) {
                      colorClass = "text-red-400";
                    } else if (log.includes("⚠️")) {
                      colorClass = "text-yellow-400";
                    } else if (log.includes("✅")) {
                      colorClass = "text-emerald-400";
                    } else if (log.includes("🔍") || log.includes("🔑")) {
                      colorClass = "text-cyan-400";
                    }

                    return (
                      <div key={i} className={`${colorClass} whitespace-pre-wrap break-words`}>
                        {log}
                      </div>
                    );
                  })
                )}
                {isAnalyzing && (
                  <div className="flex items-center gap-2 text-[#00ff41]">
                    <span className="animate-pulse">▌</span>
                  </div>
                )}
              </div>

              {/* Terminal Footer */}
              <div className="bg-[#111] border-t border-[#222] px-4 py-2 text-xs text-[#666] flex justify-between">
                <span>
                  {status?.status === "analyzing" ? "🧠 AI Analysis Running..." :
                   status?.status === "cloning" ? "📦 Cloning Repository..." :
                   status?.status === "parsing" ? "📊 Finalizing Results..." :
                   status?.status ? `Status: ${status.status}` : "Processing..."}
                </span>
                <span className="text-[#333]">NVIDIA NIM API</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
