import React, { useEffect, useRef, useState } from "react";
import { BridgeData, Citation, QueryResponse, extractBridgeData, sendQuery } from "../lib/api";
import { trackBridgeCardRendered, trackQuerySubmitted } from "../lib/datadog-rum";
import { BridgeCard } from "./BridgeCard";
import { CitationPanel } from "./CitationPanel";
import { QuerySuggestions } from "./QuerySuggestions";

interface Message {
  role: "user" | "assistant";
  content: string;
  sources: string[];
  citations: Citation[];
  bridges: BridgeData[];
  traceId?: string | null;
}

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeCitations, setActiveCitations] = useState<Citation[]>([]);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function handleSuggestionSelect(text: string) {
    setInput(text);
    inputRef.current?.focus();
  }

  async function handleSubmit(e?: React.FormEvent) {
    e?.preventDefault();
    const query = input.trim();
    if (!query || loading) return;

    setInput("");
    setError(null);
    setLoading(true);

    const userMessage: Message = {
      role: "user",
      content: query,
      sources: [],
      citations: [],
      bridges: [],
    };
    setMessages((prev) => [...prev, userMessage]);

    trackQuerySubmitted(query.length);

    try {
      const resp: QueryResponse = await sendQuery(query);
      const bridges = extractBridgeData(resp.answer);

      if (bridges.length > 0) {
        trackBridgeCardRendered(bridges.length);
      }

      const aiMessage: Message = {
        role: "assistant",
        content: resp.answer,
        sources: resp.sources,
        citations: [],
        bridges,
        traceId: resp.trace_id,
      };

      setMessages((prev) => [...prev, aiMessage]);
      setActiveCitations(aiMessage.citations);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white border-b border-gray-200 px-6 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center">
            <span className="text-white text-sm font-bold">IA</span>
          </div>
          <span className="font-semibold text-gray-900">InfraAdvisor AI</span>
        </div>
        <span className="text-xs text-gray-400">Powered by Datadog + GPT-4o</span>
      </header>

      {/* Main content: chat + citation panel */}
      <div className="flex flex-1 min-h-0">
        {/* Chat thread */}
        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
            {messages.length === 0 && (
              <div className="flex items-center justify-center h-full">
                <p className="text-sm text-gray-400">Ask about bridges, water systems, energy infrastructure, or upload a project brief.</p>
              </div>
            )}

            {messages.map((msg, i) => (
              <div
                key={i}
                data-testid={msg.role === "assistant" ? "ai-message" : "user-message"}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <div
                  className={`max-w-2xl rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                    msg.role === "user"
                      ? "bg-blue-600 text-white"
                      : "bg-white border border-gray-200 text-gray-800 shadow-sm"
                  }`}
                >
                  <p className="whitespace-pre-wrap">{msg.content}</p>

                  {/* Bridge cards */}
                  {msg.bridges.length > 0 && (
                    <div className="mt-3 space-y-2">
                      {msg.bridges.map((b, j) => (
                        <BridgeCard key={j} bridge={b} />
                      ))}
                    </div>
                  )}

                  {/* Source pills */}
                  {msg.sources.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1">
                      {msg.sources.map((s) => (
                        <span
                          key={s}
                          className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full"
                        >
                          {s}
                        </span>
                      ))}
                    </div>
                  )}

                  {msg.traceId && (
                    <p className="mt-1 text-xs text-gray-400 font-mono">trace: {msg.traceId}</p>
                  )}
                </div>
              </div>
            ))}

            {loading && (
              <div className="flex justify-start" data-testid="loading-indicator">
                <div className="bg-white border border-gray-200 rounded-2xl px-4 py-3 shadow-sm">
                  <div className="flex gap-1">
                    <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.3s]" />
                    <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce [animation-delay:-0.15s]" />
                    <div className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" />
                  </div>
                </div>
              </div>
            )}

            {error && (
              <div className="mx-auto max-w-lg bg-red-50 border border-red-200 rounded-lg px-4 py-3">
                <p className="text-sm text-red-700">{error}</p>
              </div>
            )}

            <div ref={bottomRef} />
          </div>

          {/* Query suggestions + input */}
          <div className="border-t border-gray-200 bg-white px-4 py-3 space-y-2 shrink-0">
            <QuerySuggestions onSelect={handleSuggestionSelect} disabled={loading} />
            <form onSubmit={handleSubmit} className="flex gap-2">
              <textarea
                ref={inputRef}
                data-testid="chat-input"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Ask about bridges, disasters, energy..."
                rows={1}
                disabled={loading}
                className="flex-1 resize-none rounded-xl border border-gray-300 px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:opacity-50"
              />
              <button
                type="submit"
                data-testid="send-button"
                disabled={loading || !input.trim()}
                className="rounded-xl bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed text-white px-4 py-2.5 transition-colors"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 12h14m-7-7l7 7-7 7" />
                </svg>
              </button>
            </form>
          </div>
        </div>

        {/* Citation panel (right sidebar) */}
        <div className="w-72 border-l border-gray-200 bg-white p-4 overflow-y-auto shrink-0 hidden lg:block">
          <CitationPanel citations={activeCitations} />
        </div>
      </div>
    </div>
  );
}
