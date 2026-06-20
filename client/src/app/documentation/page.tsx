"use client";

import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  BookMarked,
  Bot,
  BrainCircuit,
  Code2,
  Cpu,
  Layers3,
  Sparkles,
} from "lucide-react";

const AI_AGENTS = [
  {
    name: "Claude",
    role: "Architecture, reasoning, and system design",
    help: "Claude was used to shape the high-level architecture, refine repo analysis flows, and keep the implementation coherent across frontend and backend boundaries.",
    accent: "from-indigo-500/30 to-cyan-400/10",
    border: "border-indigo-400/40",
    icon: BrainCircuit,
  },
  {
    name: "OpenAI Codex",
    role: "Frontend velocity and UI implementation",
    help: "Codex was used to rapidly iterate the landing page, results dashboard, graph presentation, and interactive UI behaviors with a focus on hackathon speed and visual impact.",
    accent: "from-emerald-500/30 to-lime-400/10",
    border: "border-emerald-400/40",
    icon: Code2,
  },
  {
    name: "Google Gemini",
    role: "Embedding and retrieval strategy",
    help: "Gemini influenced the earlier retrieval approach and helped frame how repository context could be represented compactly for AI-assisted querying.",
    accent: "from-sky-500/30 to-blue-400/10",
    border: "border-sky-400/40",
    icon: Cpu,
  },
  {
    name: "GitHub Copilot",
    role: "Iteration speed and code cleanup",
    help: "Copilot helped tighten component structure, refine styling details, and keep the product moving quickly while polishing the user experience.",
    accent: "from-fuchsia-500/30 to-pink-400/10",
    border: "border-fuchsia-400/40",
    icon: Bot,
  },
];

const PIPELINE = [
  {
    title: "Clone and normalize",
    text: "The backend clones the repository into a temporary workspace and normalizes the file list so the UI can work from a stable snapshot.",
  },
  {
    title: "Analyze and score",
    text: "The analyzer extracts dependencies, hotspots, vulnerabilities, and graph relationships from the cloned repo before handing results to the frontend.",
  },
  {
    title: "Visualize and interrogate",
    text: "The results page renders the graph, dependency summaries, file tree, and repo-aware chat in a single analysis workspace.",
  },
];

export default function DocumentationPage() {
  const router = useRouter();

  return (
    <main className="min-h-screen bg-[#050505] text-[#f0f0f0] relative overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(0,255,65,0.16),transparent_30%),radial-gradient(circle_at_bottom_right,rgba(59,130,246,0.12),transparent_35%)] pointer-events-none" />
      <div className="absolute inset-0 opacity-[0.06] pointer-events-none bg-[linear-gradient(rgba(255,255,255,0.08)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.08)_1px,transparent_1px)] bg-[size:56px_56px]" />

      <div className="relative z-10 max-w-7xl mx-auto px-6 py-8">
        <button
          onClick={() => router.push("/login")}
          className="inline-flex items-center gap-2 px-4 py-2 rounded-md border border-[#00ff41]/40 bg-[#00ff41]/8 text-[#00ff41] hover:bg-[#00ff41]/14 transition-all mb-10"
        >
          <ArrowLeft size={16} /> Back
        </button>

        <section className="grid lg:grid-cols-[1.25fr_0.75fr] gap-8 items-start">
          <div className="space-y-6">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-[#00ff41]/30 bg-[#00ff41]/10 text-[#00ff41] text-xs uppercase tracking-[0.28em] font-bold">
              <Sparkles size={12} /> Documentation
            </div>
            <div>
              <h1 className="text-5xl md:text-7xl font-black tracking-tight leading-none">
                Built With <span className="text-[#00ff41]">AI Agents</span>
              </h1>
              <p className="mt-5 text-lg md:text-xl text-[#a8a8a8] max-w-3xl leading-relaxed">
                This project was assembled through a multi-model workflow. Different AI systems were used for different parts of the product, and this page documents what each one contributed.
              </p>
            </div>
          </div>

          <div className="glass border border-[#00ff41]/25 rounded-3xl p-6 md:p-8 shadow-2xl shadow-black/40">
            <div className="flex items-center gap-3 mb-5">
              <BookMarked className="text-[#00ff41]" size={22} />
              <div>
                <h2 className="text-xl font-bold">Hackathon Strategy</h2>
                <p className="text-sm text-[#8b8b8b]">Deliberately aggressive UI, fast iteration, and clear product signaling.</p>
              </div>
            </div>
            <div className="space-y-4 text-sm leading-relaxed text-[#cfcfcf]">
              <p>
                CodeLens is designed to feel sharper and more ambitious than a standard demo: high-contrast neon styling, layered glass panels, animated background effects, and a dense information layout.
              </p>
              <p>
                The goal was to make the project look like a serious hackathon contender while still keeping the workflow understandable for judges and users.
              </p>
            </div>
          </div>
        </section>

        <section className="mt-16 grid md:grid-cols-3 gap-5">
          {PIPELINE.map((item, index) => (
            <div key={item.title} className="rounded-2xl border border-[#222] bg-[#0a0a0a]/90 p-6 relative overflow-hidden">
              <div className="absolute top-0 right-0 w-24 h-24 bg-[#00ff41]/10 blur-3xl rounded-full" />
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-lg border border-[#00ff41]/30 bg-[#00ff41]/10 flex items-center justify-center text-[#00ff41] font-bold">
                  0{index + 1}
                </div>
                <h3 className="text-lg font-bold">{item.title}</h3>
              </div>
              <p className="text-sm text-[#9b9b9b] leading-relaxed">{item.text}</p>
            </div>
          ))}
        </section>

        <section className="mt-16">
          <div className="flex items-center gap-3 mb-6">
            <Layers3 className="text-[#00ff41]" size={22} />
            <h2 className="text-2xl md:text-3xl font-bold">Model Contributions</h2>
          </div>

          <div className="grid lg:grid-cols-2 gap-6">
            {AI_AGENTS.map((agent) => {
              const Icon = agent.icon;
              return (
                <article key={agent.name} className={`rounded-3xl border ${agent.border} bg-[#0a0a0a]/90 overflow-hidden shadow-2xl shadow-black/30`}>
                  <div className={`p-6 bg-gradient-to-br ${agent.accent}`}>
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <div className="flex items-center gap-3 mb-3">
                          <div className="w-11 h-11 rounded-xl bg-black/30 border border-white/10 flex items-center justify-center">
                            <Icon size={20} className="text-[#f4f4f4]" />
                          </div>
                          <h3 className="text-2xl font-black">{agent.name}</h3>
                        </div>
                        <p className="text-sm uppercase tracking-[0.22em] text-[#d9d9d9] font-bold">{agent.role}</p>
                      </div>
                      <div className="px-3 py-1 rounded-full bg-black/20 border border-white/10 text-xs font-bold text-[#f2f2f2]">
                        Agent
                      </div>
                    </div>
                  </div>
                  <div className="p-6 space-y-4">
                    <p className="text-sm md:text-base text-[#d2d2d2] leading-relaxed">{agent.help}</p>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      </div>
    </main>
  );
}
