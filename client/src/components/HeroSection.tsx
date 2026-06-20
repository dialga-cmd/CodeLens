"use client";

import React, { useEffect, useRef } from "react";

const colors = {
  50: "#f8f7f5",
  100: "#e6e1d7",
  200: "#00ff41", // Changed to CodeLens green
  300: "#a89080",
  400: "#8a7060",
  500: "#003b11", // Changed to dark green
  600: "#544237",
  700: "#3c4237",
  800: "#2a2e26",
  900: "#1a1d18",
};

export function HeroSection({ onGetStarted }: { onGetStarted?: () => void }) {
  const gradientRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Animate words
    const words = document.querySelectorAll<HTMLElement>(".word");
    words.forEach((word) => {
      const delay = parseInt(word.getAttribute("data-delay") || "0", 10);
      setTimeout(() => {
        word.style.animation = "word-appear 0.8s ease-out forwards";
      }, delay);
    });

    // Mouse gradient
    const gradient = gradientRef.current;
    function onMouseMove(e: MouseEvent) {
      if (gradient) {
        gradient.style.left = e.clientX - 192 + "px";
        gradient.style.top = e.clientY - 192 + "px";
        gradient.style.opacity = "1";
      }
    }
    function onMouseLeave() {
      if (gradient) gradient.style.opacity = "0";
    }
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseleave", onMouseLeave);

    // Word hover effects
    words.forEach((word) => {
      word.addEventListener("mouseenter", () => {
        word.style.textShadow = "0 0 20px rgba(0, 255, 65, 0.5)"; // CodeLens green
      });
      word.addEventListener("mouseleave", () => {
        word.style.textShadow = "none";
      });
    });

    // Click ripple effect
    function onClick(e: MouseEvent) {
      const ripple = document.createElement("div");
      ripple.style.position = "fixed";
      ripple.style.left = e.clientX + "px";
      ripple.style.top = e.clientY + "px";
      ripple.style.width = "4px";
      ripple.style.height = "4px";
      ripple.style.background = "rgba(0, 255, 65, 0.6)"; // CodeLens green
      ripple.style.borderRadius = "50%";
      ripple.style.transform = "translate(-50%, -50%)";
      ripple.style.pointerEvents = "none";
      ripple.style.animation = "pulse-glow 1s ease-out forwards";
      document.body.appendChild(ripple);
      setTimeout(() => ripple.remove(), 1000);
    }
    document.addEventListener("click", onClick);

    // Floating elements on scroll
    let scrolled = false;
    function onScroll() {
      if (!scrolled) {
        scrolled = true;
        document.querySelectorAll<HTMLElement>(".floating-element").forEach((el, index) => {
          setTimeout(() => {
            el.style.animationPlayState = "running";
          }, index * 200);
        });
      }
    }
    window.addEventListener("scroll", onScroll);

    return () => {
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseleave", onMouseLeave);
      document.removeEventListener("click", onClick);
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  return (
    <div
      className="h-[80vh] md:h-[90vh] bg-gradient-to-br from-[#050505] via-[#080808] to-[#111111] text-[#e6e1d7] font-primary overflow-hidden relative w-full flex items-center justify-center"
    >
      <svg className="absolute inset-0 w-full h-full" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <pattern id="grid" width="60" height="60" patternUnits="userSpaceOnUse">
            <path
              d="M 60 0 L 0 0 0 60"
              fill="none"
              stroke="rgba(0, 255, 65, 0.08)"
              strokeWidth="0.5"
            />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill="url(#grid)" />
        <line x1="0" y1="20%" x2="100%" y2="20%" className="grid-line" style={{ animationDelay: "0.5s" }} />
        <line x1="0" y1="80%" x2="100%" y2="80%" className="grid-line" style={{ animationDelay: "1s" }} />
        <line x1="20%" y1="0" x2="20%" y2="100%" className="grid-line" style={{ animationDelay: "1.5s" }} />
        <line x1="80%" y1="0" x2="80%" y2="100%" className="grid-line" style={{ animationDelay: "2s" }} />
        <line
          x1="50%"
          y1="0"
          x2="50%"
          y2="100%"
          className="grid-line"
          style={{ animationDelay: "2.5s", opacity: 0.05 }}
        />
        <line
          x1="0"
          y1="50%"
          x2="100%"
          y2="50%"
          className="grid-line"
          style={{ animationDelay: "3s", opacity: 0.05 }}
        />
        <circle cx="20%" cy="20%" r="2" className="detail-dot" style={{ animationDelay: "3s" }} />
        <circle cx="80%" cy="20%" r="2" className="detail-dot" style={{ animationDelay: "3.2s" }} />
        <circle cx="20%" cy="80%" r="2" className="detail-dot" style={{ animationDelay: "3.4s" }} />
        <circle cx="80%" cy="80%" r="2" className="detail-dot" style={{ animationDelay: "3.6s" }} />
        <circle cx="50%" cy="50%" r="1.5" className="detail-dot" style={{ animationDelay: "4s" }} />
      </svg>

      {/* Corner elements */}
      <div className="corner-element top-8 left-8" style={{ animationDelay: "4s" }}>
        <div
          className="absolute top-0 left-0 w-2 h-2 opacity-30"
          style={{ background: colors[200] }}
        ></div>
      </div>
      <div className="corner-element top-8 right-8" style={{ animationDelay: "4.2s" }}>
        <div
          className="absolute top-0 right-0 w-2 h-2 opacity-30"
          style={{ background: colors[200] }}
        ></div>
      </div>
      <div className="corner-element bottom-8 left-8" style={{ animationDelay: "4.4s" }}>
        <div
          className="absolute bottom-0 left-0 w-2 h-2 opacity-30"
          style={{ background: colors[200] }}
        ></div>
      </div>
      <div className="corner-element bottom-8 right-8" style={{ animationDelay: "4.6s" }}>
        <div
          className="absolute bottom-0 right-0 w-2 h-2 opacity-30"
          style={{ background: colors[200] }}
        ></div>
      </div>

      {/* Floating elements */}
      <div className="floating-element" style={{ top: "25%", left: "15%", animationDelay: "5s", background: colors[200] }}></div>
      <div className="floating-element" style={{ top: "60%", left: "85%", animationDelay: "5.5s", background: colors[200] }}></div>
      <div className="floating-element" style={{ top: "40%", left: "10%", animationDelay: "6s", background: colors[200] }}></div>
      <div className="floating-element" style={{ top: "75%", left: "90%", animationDelay: "6.5s", background: colors[200] }}></div>

      <div className="relative z-10 flex flex-col justify-center items-center px-8 md:px-16 w-full mt-16">
        {/* Top tagline */}
        <div className="text-center">
          <h2
            className="text-xs md:text-sm font-mono font-bold uppercase tracking-[0.2em] opacity-80"
            style={{ color: colors[200] }}
          >
            <span className="word" data-delay="0">
              Welcome
            </span>
            <span className="word" data-delay="200">
              to
            </span>
            <span className="word" data-delay="400">
              <b>CodeLens</b>
            </span>
            <span className="word" data-delay="600">
              —
            </span>
            <span className="word" data-delay="800">
              Industrial
            </span>
            <span className="word" data-delay="1000">
              Codebase
            </span>
            <span className="word" data-delay="1200">
              Intelligence.
            </span>
          </h2>
          <div
            className="mt-4 mx-auto w-16 h-px opacity-30"
            style={{
              background: `linear-gradient(to right, transparent, ${colors[200]}, transparent)`,
            }}
          ></div>
        </div>

        {/* Main headline */}
        <div className="text-center max-w-5xl mx-auto mt-8 mb-8 relative">
          <h1
            className="text-4xl md:text-6xl lg:text-7xl font-extrabold leading-tight tracking-tight text-white drop-shadow-lg"
          >
            <div className="mb-4 md:mb-6">
              <span className="word" data-delay="1600">
                Understand
              </span>
              <span className="word" data-delay="1750">
                Any
              </span>
              <span className="word" data-delay="1900" style={{ color: colors[200] }}>
                Codebase
              </span>
              <span className="word" data-delay="2050">
                Instantly.
              </span>
            </div>
            <div
              className="text-2xl md:text-3xl lg:text-4xl font-light leading-relaxed text-[#aaa]"
            >
              <span className="word" data-delay="2300">
                Visualize
              </span>
              <span className="word" data-delay="2450">
                module
              </span>
              <span className="word" data-delay="2600">
                dependencies
              </span>
              <span className="word" data-delay="2750">
                in
              </span>
              <span className="word" data-delay="2900">
                a
              </span>
              <span className="word" data-delay="3050">
                fully
              </span>
              <span className="word" data-delay="3200">
                interactive
              </span>
              <span className="word" data-delay="3350">
                3D
              </span>
              <span className="word" data-delay="3500">
                environment.
              </span>
            </div>
          </h1>
          <div
            className="absolute -left-8 top-1/2 w-4 h-px opacity-20"
            style={{
              background: colors[200],
              animation: "word-appear 1s ease-out forwards",
              animationDelay: "3.5s",
            }}
          ></div>
          <div
            className="absolute -right-8 top-1/2 w-4 h-px opacity-20"
            style={{
              background: colors[200],
              animation: "word-appear 1s ease-out forwards",
              animationDelay: "3.7s",
            }}
          ></div>
        </div>

        {/* Bottom tagline */}
        <div className="text-center mt-20">
          <div
            className="mb-4 mx-auto w-16 h-px opacity-30"
            style={{
              background: `linear-gradient(to right, transparent, ${colors[200]}, transparent)`,
            }}
          ></div>
          <h2
            className="text-xs md:text-sm font-mono font-light uppercase tracking-[0.2em] opacity-80"
            style={{ color: colors[200] }}
          >
            <span className="word" data-delay="3800">
              Automated
            </span>
            <span className="word" data-delay="3950">
              security
            </span>
            <span className="word" data-delay="4100">
              audits,
            </span>
            <span className="word" data-delay="4250">
              AI-driven
            </span>
            <span className="word" data-delay="4400">
              insights,
            </span>
            <span className="word" data-delay="4550">
              enterprise
            </span>
            <span className="word" data-delay="4700">
              scale.
            </span>
          </h2>
          <div
            className="mt-6 flex justify-center space-x-4 opacity-0"
            style={{
              animation: "word-appear 1s ease-out forwards",
              animationDelay: "4.5s",
            }}
          >
            <div
              className="w-1 h-1 rounded-full opacity-40"
              style={{ background: colors[200] }}
            ></div>
            <div
              className="w-1 h-1 rounded-full opacity-60"
              style={{ background: colors[200] }}
            ></div>
            <div
              className="w-1 h-1 rounded-full opacity-40"
              style={{ background: colors[200] }}
            ></div>
          </div>
        </div>

        {/* Call to action */}
        <div
          className="mt-12 opacity-0 flex gap-4"
          style={{
            animation: "word-appear 1s ease-out forwards",
            animationDelay: "5s",
          }}
        >
          <button
            onClick={onGetStarted}
            className="glow-button bg-[#00ff41] text-black font-extrabold px-10 py-4 rounded-sm flex items-center gap-3 transition-all cursor-pointer relative z-20"
          >
            CONTINUE WITH GOOGLE
          </button>
        </div>
      </div>

      <div
        id="mouse-gradient"
        ref={gradientRef}
        className="fixed pointer-events-none w-96 h-96 rounded-full blur-3xl transition-all duration-500 ease-out opacity-0 z-0"
        style={{
          background: `radial-gradient(circle, ${colors[500]}60 0%, transparent 100%)`,
        }}
      ></div>
    </div>
  );
}
