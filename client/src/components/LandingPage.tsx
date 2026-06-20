"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Cookies from "js-cookie";
import {
  Code2,
  Github,
  ArrowRight,
  Layers,
  Cpu,
  ShieldCheck,
  Zap,
  Activity,
  Search,
  ChevronRight,
  Globe,
  Terminal
} from "lucide-react";

import DottedSurface from "./DottedSurface";
import { HeroSection } from "./HeroSection";

export default function LandingPage({ onGetStarted }: { onGetStarted: () => Promise<any> }) {
  const [scrolled, setScrolled] = useState(false);
  const router = useRouter();

  const handleGetStarted = async () => {
    const hasCookie = Cookies.get("codelens_user");
    if (hasCookie) {
      router.push("/dashboard");
    } else {
      try {
        await onGetStarted();
        router.push("/dashboard");
      } catch (err) {
        console.error(err);
      }
    }
  };

  useEffect(() => {
    const handleScroll = () => setScrolled(window.scrollY > 50);
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, []);

  return (
    <div className="min-h-screen bg-[#050505] text-[#f0f0f0] selection:bg-[#00ff41] selection:text-black">
      {/* Background Layer */}
      <div className="fixed inset-0 pointer-events-none z-0">
        <div className="absolute inset-0 grid-bg opacity-10" />
        <DottedSurface />
      </div>

      {/* Navbar */}
      <nav className={`fixed top-0 w-full z-50 transition-all duration-300 ${scrolled ? "glass py-4 shadow-2xl" : "py-8"}`}>
        <div className="max-w-7xl mx-auto px-6 flex justify-between items-center">
          <div className="flex items-center gap-2 group cursor-pointer">
            <div className="bg-[#00ff41] p-1.5 rounded-sm group-hover:rotate-12 transition-transform">
              <Code2 size={20} className="text-black" />
            </div>
            <span className="text-xl font-changa tracking-widest font-bold">CODELENS</span>
          </div>

          <div className="hidden md:flex items-center gap-8 text-sm font-medium text-[#888]">
            <a href="#features" className="hover:text-[#00ff41] transition-colors">Features</a>
            <a href="#workflow" className="hover:text-[#00ff41] transition-colors">Workflow</a>
            <a href="#security" className="hover:text-[#00ff41] transition-colors">Security</a>
            <button
              onClick={handleGetStarted}
              className="bg-[#111] border border-[#222] px-5 py-2 rounded hover:border-[#00ff41] hover:text-[#00ff41] transition-all"
            >
              Sign In
            </button>
          </div>
        </div>
      </nav>

      <HeroSection onGetStarted={handleGetStarted} />


      {/* Features Grid */}
      <section id="features" className="py-32 px-6">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-20">
            <h2 className="text-4xl md:text-5xl font-outfit font-bold mb-4">Engineered for <span className="text-[#00ff41]">Scale.</span></h2>
            <p className="text-[#888] max-w-xl mx-auto">Deep architectural insights powered by AI-driven graph analysis.</p>
          </div>

          <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6">
            <FeatureCard
              icon={<Layers className="text-[#00ff41]" />}
              title="3D Logic Mapping"
              desc="Visualize function calls and module dependencies in a fully interactive 3D spatial environment."
            />
            <FeatureCard
              icon={<Cpu className="text-[#00ff41]" />}
              title="AI Code Oracle"
              desc="Ask deep architectural questions. Our repo-aware AI understands your logic better than documentation."
            />
            <FeatureCard
              icon={<ShieldCheck className="text-[#00ff41]" />}
              title="Security Audit"
              desc="Automated vulnerability scanning using AST analysis to find potential exploits before they ship."
            />
            <FeatureCard
              icon={<Activity className="text-[#00ff41]" />}
              title="Hotspot Detection"
              desc="Identify circular dependencies and high-complexity modules that risk your system stability."
            />
          </div>
        </div>
      </section>

      {/* Workflow Section */}
      <section id="workflow" className="py-32 px-6 relative">
        <div className="max-w-7xl mx-auto grid md:grid-cols-2 gap-20 items-center">
          <div>
            <span className="text-[#00ff41] font-mono text-sm tracking-widest uppercase mb-4 block text-glow">The Process</span>
            <h2 className="text-4xl md:text-5xl font-outfit font-bold mb-8 leading-tight">From Repo to <br /> <span className="gradient-text">Deep Intelligence.</span></h2>

            <div className="space-y-12">
              <Step title="Ingest" desc="Connect any public GitHub repository URL. Our ingestion engine clones and indexes it in seconds." />
              <Step title="Analyze" desc="We parse the AST, extract symbols, and build a multi-dimensional semantic map of your logic." />
              <Step title="Visualize" desc="Explore the codebase through an immersive 3D graph interface that reveals hidden patterns." />
            </div>
          </div>

          <div className="relative">
            <div className="glass p-8 rounded-2xl border-[#00ff41]/20 relative z-10">
              <div className="flex items-center gap-3 mb-6">
                <Terminal size={20} className="text-[#00ff41]" />
                <span className="font-mono text-xs text-[#555]">codelens_cli --analyze</span>
              </div>
              <div className="space-y-3 font-mono text-[11px] leading-relaxed">
                <div className="text-[#00ff41]">[SYSTEM] Initializing Ingestion Engine...</div>
                <div className="text-[#888]">[FETCH] Cloning repository...</div>
                <div className="text-[#888]">[PARSE] Analyzing AST structures...</div>
                <div className="text-[#888]">[INDEX] Building compact repo snapshot...</div>
                <div className="text-[#00ff41]">[OK] Analysis Complete.</div>
                <div className="text-white mt-4 animate-pulse">{">"} READY FOR INTERROGATION_</div>
              </div>
            </div>
            <div className="absolute -top-10 -right-10 w-64 h-64 border border-[#00ff41]/10 rounded-full animate-spin-slow pointer-events-none" />
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-32 px-6 text-center">
        <div className="max-w-3xl mx-auto glass p-16 rounded-3xl border-[#00ff41]/30 relative overflow-hidden">
          <div className="absolute top-0 right-0 w-32 h-32 bg-[#00ff41]/5 blur-3xl rounded-full" />
          <h2 className="text-4xl md:text-5xl font-outfit font-bold mb-6">Ready to see <span className="text-[#00ff41]">clearly?</span></h2>
          <p className="text-[#888] mb-10 text-lg">Join developers who use CodeLens to master complex legacy systems and audit new projects.</p>
          <button
            onClick={onGetStarted}
            className="glow-button bg-[#00ff41] text-black font-extrabold px-12 py-5 rounded-sm inline-flex items-center gap-3 transition-all"
          >
            GET STARTED FOR FREE <ChevronRight size={20} />
          </button>
        </div>
      </section>

      {/* Footer */}
      <footer className="py-20 px-6 border-t border-[#111]">
        <div className="max-w-7xl mx-auto flex flex-col md:flex-row justify-between items-start gap-12">
          <div className="space-y-6">
            <div className="flex items-center gap-2">
              <Code2 size={24} className="text-[#00ff41]" />
              <span className="text-xl font-changa tracking-widest font-bold">CODELENS</span>
            </div>
            <p className="text-[#444] text-sm max-w-xs font-medium">
              Industrial grade code intelligence. Built for the next generation of software architects.
            </p>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-3 gap-16">
            <FooterLinkGroup title="Product" links={["3D Visualization", "AI Assistant", "Security Audit", "API"]} />
            <FooterLinkGroup title="Company" links={["About", "Security", "Privacy", "Terms"]} />
            <FooterLinkGroup title="Connect" links={["Github", "Email"]} />
          </div>
        </div>
        <div className="max-w-7xl mx-auto mt-20 pt-8 border-t border-[#111] flex justify-between items-center text-[10px] font-bold tracking-[0.2em] text-[#222] uppercase">
          <span>&copy; 2026 CODELENS INTELLIGENCE PLATFORM</span>
          <span>SYSTEM_STATUS: OPTIMAL</span>
        </div>
      </footer>
    </div>
  );
}

function FeatureCard({ icon, title, desc }: { icon: React.ReactNode, title: string, desc: string }) {
  return (
    <div className="feature-card glass p-8 rounded-xl space-y-4">
      <div className="bg-[#050505] w-12 h-12 rounded-lg flex items-center justify-center border border-[#222]">
        {icon}
      </div>
      <h3 className="text-xl font-bold">{title}</h3>
      <p className="text-[#666] text-sm leading-relaxed">{desc}</p>
    </div>
  );
}

function Step({ title, desc }: { title: string, desc: string }) {
  return (
    <div className="flex gap-6 group">
      <div className="space-y-2">
        <h4 className="text-xl font-bold group-hover:text-[#00ff41] transition-colors flex items-center gap-3">
          <div className="w-1.5 h-1.5 rounded-full bg-[#00ff41]" /> {title}
        </h4>
        <p className="text-[#888] text-sm leading-relaxed">{desc}</p>
      </div>
    </div>
  );
}

function FooterLinkGroup({ title, links }: { title: string, links: string[] }) {
  return (
    <div className="space-y-6">
      <h5 className="text-[10px] font-bold text-[#333] uppercase tracking-[0.3em]">{title}</h5>
      <ul className="space-y-4 text-sm text-[#666]">
        {links.map(link => (
          <li key={link}><a href="#" className="hover:text-[#00ff41] transition-colors">{link}</a></li>
        ))}
      </ul>
    </div>
  );
}
