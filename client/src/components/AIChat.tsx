"use client";

import { useEffect, useRef, useCallback, useState, useTransition } from "react";
import { cn } from "@/lib/utils";
import {
    Bot,
    SendIcon,
    LoaderIcon,
    Sparkles,
    User as UserIcon,
} from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import * as React from "react";
import { User as FirebaseUser } from "firebase/auth";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface UseAutoResizeTextareaProps {
    minHeight: number;
    maxHeight?: number;
}

function useAutoResizeTextarea({
    minHeight,
    maxHeight,
}: UseAutoResizeTextareaProps) {
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const adjustHeight = useCallback(
        (reset?: boolean) => {
            const textarea = textareaRef.current;
            if (!textarea) return;

            if (reset) {
                textarea.style.height = `${minHeight}px`;
                return;
            }

            textarea.style.height = `${minHeight}px`;
            const newHeight = Math.max(
                minHeight,
                Math.min(
                    textarea.scrollHeight,
                    maxHeight ?? Number.POSITIVE_INFINITY
                )
            );

            textarea.style.height = `${newHeight}px`;
        },
        [minHeight, maxHeight]
    );

    useEffect(() => {
        const textarea = textareaRef.current;
        if (textarea) {
            textarea.style.height = `${minHeight}px`;
        }
    }, [minHeight]);

    return { textareaRef, adjustHeight };
}

interface TextareaProps
  extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  containerClassName?: string;
  showRing?: boolean;
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, containerClassName, showRing = true, ...props }, ref) => {
    const [isFocused, setIsFocused] = React.useState(false);
    
    return (
      <div className={cn(
        "relative",
        containerClassName
      )}>
        <textarea
          className={cn(
            "flex min-h-[80px] w-full rounded-md border border-[#222] bg-transparent px-3 py-2 text-sm",
            "transition-all duration-200 ease-in-out",
            "placeholder:text-[#666]",
            "disabled:cursor-not-allowed disabled:opacity-50",
            showRing ? "focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0" : "",
            className
          )}
          ref={ref}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          {...props}
        />
        
        {showRing && isFocused && (
          <motion.span 
            className="absolute inset-0 rounded-md pointer-events-none ring-2 ring-offset-0 ring-violet-500/30"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          />
        )}
      </div>
    )
  }
)
Textarea.displayName = "Textarea"

export default function AIChat({ repoId, user, className }: { repoId: string, user: FirebaseUser, className?: string }) {
    const [messages, setMessages] = useState<Message[]>([]);
    const [value, setValue] = useState("");
    const [isTyping, setIsTyping] = useState(false);
    const [mousePosition, setMousePosition] = useState({ x: 0, y: 0 });
    const { textareaRef, adjustHeight } = useAutoResizeTextarea({
        minHeight: 40,
        maxHeight: 120,
    });
    const [inputFocused, setInputFocused] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages, isTyping]);

    useEffect(() => {
        const handleMouseMove = (e: MouseEvent) => {
            // Adjust position relative to the chat container
            const rect = document.getElementById("chat-container")?.getBoundingClientRect();
            if (rect) {
                setMousePosition({ x: e.clientX - rect.left, y: e.clientY - rect.top });
            }
        };

        window.addEventListener('mousemove', handleMouseMove);
        return () => {
            window.removeEventListener('mousemove', handleMouseMove);
        };
    }, []);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            if (value.trim()) {
                handleSendMessage();
            }
        }
    };

    const handleSendMessage = async () => {
        if (!value.trim() || isTyping) return;

        const query = value.trim();
        const userMessage: Message = { role: "user", content: query };
        setMessages(prev => [...prev, userMessage]);
        setValue("");
        adjustHeight(true);
        setIsTyping(true);

        try {
            const token = await user.getIdToken();
            const response = await fetch(`${API_BASE_URL}/chat`, {
                method: "POST",
                headers: { 
                    "Content-Type": "application/json",
                    "Authorization": `Bearer ${token}`
                },
                body: JSON.stringify({ repo_id: repoId, query }),
            });

            const data = await response.json();
            const assistantMessage: Message = { role: "assistant", content: data.answer };
            setMessages(prev => [...prev, assistantMessage]);
        } catch (error) {
            setMessages(prev => [...prev, { role: "assistant", content: "Error: Could not reach the AI server." }]);
        } finally {
            setIsTyping(false);
        }
    };

    return (
        <div id="chat-container" className={`panel h-full min-h-0 flex flex-col bg-[#050505] border-[#222] relative overflow-hidden ${className ?? ""}`}>
            <div className="absolute inset-0 w-full h-full overflow-hidden pointer-events-none">
                <div className="absolute top-0 left-1/4 w-64 h-64 bg-[#00ff41]/5 rounded-full mix-blend-normal filter blur-[100px] animate-pulse" />
                <div className="absolute bottom-0 right-1/4 w-64 h-64 bg-[#00ff41]/5 rounded-full mix-blend-normal filter blur-[100px] animate-pulse delay-700" />
            </div>

            <div className="p-3 border-b border-[#222] flex items-center justify-between relative z-10 bg-black/40 backdrop-blur-md">
                <h3 className="text-xs font-bold text-[#444] uppercase tracking-widest flex items-center gap-2">
                    <Sparkles size={14} className="text-[#00ff41]" /> Code Intelligence Chat
                </h3>
                <span className="text-[10px] text-[#00ff41] bg-[#003b11] px-2 py-0.5 rounded border border-[#00ff41]/30 shadow-[0_0_10px_rgba(0,255,65,0.2)]">Repo Context</span>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4 font-mono text-[11px] relative z-10 scrollbar-thin scrollbar-thumb-[#222] scrollbar-track-transparent">
                {messages.length === 0 && (
                    <motion.div 
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="h-full flex flex-col items-center justify-center space-y-4 mt-8"
                    >
                        <div className="w-16 h-16 rounded-full bg-[#00ff41]/10 flex items-center justify-center border border-[#00ff41]/20 shadow-[0_0_30px_rgba(0,255,65,0.1)]">
                            <Bot size={32} className="text-[#00ff41]" />
                        </div>
                        <h1 className="text-lg font-medium tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white/90 to-white/40">
                            How can I help you analyze this repo today?
                        </h1>
                    </motion.div>
                )}
                
                {messages.map((msg, i) => (
                    <motion.div 
                        initial={{ opacity: 0, y: 10 }}
                        animate={{ opacity: 1, y: 0 }}
                        key={i} 
                        className={`flex gap-3 ${msg.role === "user" ? "justify-end" : ""}`}
                    >
                        {msg.role === "assistant" && (
                            <div className="w-6 h-6 rounded-full bg-[#111] border border-[#222] flex items-center justify-center text-[#00ff41] shrink-0 shadow-[0_0_10px_rgba(0,255,65,0.1)]">
                                <Bot size={12} />
                            </div>
                        )}
                        <div className={`max-w-[85%] min-w-0 break-words p-3 rounded-2xl ${
                            msg.role === "user" 
                            ? "bg-[#00ff41]/10 text-[#00ff41] border border-[#00ff41]/20 rounded-tr-sm shadow-[0_0_15px_rgba(0,255,65,0.05)]" 
                            : "bg-[#111]/80 backdrop-blur-sm text-[#ddd] border border-[#222] rounded-tl-sm shadow-xl"
                        }`}>
                            {msg.role === "assistant" ? (
                                <div className="prose prose-invert prose-sm max-w-none prose-p:leading-relaxed prose-pre:bg-[#050505] prose-pre:border prose-pre:border-[#333] prose-pre:p-3 prose-pre:overflow-x-auto prose-code:text-[#00ff41] prose-a:text-[#00ff41]">
                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                        {msg.content}
                                    </ReactMarkdown>
                                </div>
                            ) : (
                                msg.content
                            )}
                        </div>
                        {msg.role === "user" && (
                            <div className="w-6 h-6 rounded-full bg-[#222] border border-[#333] flex items-center justify-center text-[#888] shrink-0">
                                <UserIcon size={12} />
                            </div>
                        )}
                    </motion.div>
                ))}
                
                <AnimatePresence>
                    {isTyping && (
                        <motion.div 
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, scale: 0.9 }}
                            className="flex gap-3"
                        >
                            <div className="w-6 h-6 rounded-full bg-[#111] border border-[#222] flex items-center justify-center text-[#00ff41] shrink-0 shadow-[0_0_10px_rgba(0,255,65,0.1)]">
                                <Bot size={12} />
                            </div>
                            <div className="bg-[#111]/80 backdrop-blur-sm border border-[#222] rounded-2xl rounded-tl-sm p-3 flex items-center gap-2 text-sm text-[#00ff41]/70 w-24 shadow-xl">
                                <TypingDots />
                            </div>
                        </motion.div>
                    )}
                </AnimatePresence>
                <div ref={messagesEndRef} />
            </div>

            <div className="p-4 relative z-20">
                <motion.div 
                    className="relative backdrop-blur-2xl bg-[#0a0a0a]/80 rounded-2xl border border-[#222] shadow-2xl overflow-hidden"
                    initial={{ scale: 0.98 }}
                    animate={{ scale: 1 }}
                    transition={{ delay: 0.1 }}
                >
                    <div className="p-2">
                        <Textarea
                            ref={textareaRef}
                            value={value}
                            onChange={(e) => {
                                setValue(e.target.value);
                                adjustHeight();
                            }}
                            onKeyDown={handleKeyDown}
                            onFocus={() => setInputFocused(true)}
                            onBlur={() => setInputFocused(false)}
                            placeholder="Ask CodeLens a question..."
                            containerClassName="w-full"
                            className={cn(
                                "w-full px-3 py-2",
                                "resize-none",
                                "bg-transparent",
                                "border-none",
                                "text-white/90 text-sm font-mono",
                                "focus:outline-none",
                                "placeholder:text-white/20",
                                "min-h-[40px]"
                            )}
                            style={{
                                overflow: "hidden",
                            }}
                            showRing={false}
                        />
                    </div>

                    <div className="p-2 border-t border-[#222] flex items-center justify-end bg-[#050505]/50">
                        <motion.button
                            type="button"
                            onClick={handleSendMessage}
                            whileHover={{ scale: 1.02 }}
                            whileTap={{ scale: 0.98 }}
                            disabled={isTyping || !value.trim()}
                            className={cn(
                                "px-4 py-1.5 rounded-xl text-xs font-bold transition-all uppercase tracking-wider",
                                "flex items-center gap-2",
                                value.trim()
                                    ? "bg-[#00ff41] text-black shadow-[0_0_15px_rgba(0,255,65,0.4)]"
                                    : "bg-[#111] text-[#444] border border-[#222]"
                            )}
                        >
                            {isTyping ? (
                                <LoaderIcon className="w-3 h-3 animate-[spin_2s_linear_infinite]" />
                            ) : (
                                <SendIcon className="w-3 h-3" />
                            )}
                            <span>Send</span>
                        </motion.button>
                    </div>
                </motion.div>
            </div>

            {inputFocused && (
                <motion.div 
                    className="absolute w-[30rem] h-[30rem] rounded-full pointer-events-none z-0 opacity-[0.03] bg-gradient-to-r from-[#00ff41] via-[#00cc33] to-[#009922] blur-[64px]"
                    animate={{
                        x: mousePosition.x - 240,
                        y: mousePosition.y - 240,
                    }}
                    transition={{
                        type: "spring",
                        damping: 25,
                        stiffness: 150,
                        mass: 0.5,
                    }}
                />
            )}
        </div>
    );
}

function TypingDots() {
    return (
        <div className="flex items-center ml-1">
            {[1, 2, 3].map((dot) => (
                <motion.div
                    key={dot}
                    className="w-1 h-1 bg-[#00ff41]/90 rounded-full mx-0.5"
                    initial={{ opacity: 0.3 }}
                    animate={{ 
                        opacity: [0.3, 0.9, 0.3],
                        scale: [0.85, 1.1, 0.85]
                    }}
                    transition={{
                        duration: 1.2,
                        repeat: Infinity,
                        delay: dot * 0.15,
                        ease: "easeInOut",
                    }}
                    style={{
                        boxShadow: "0 0 4px rgba(0, 255, 65, 0.3)"
                    }}
                />
            ))}
        </div>
    );
}
