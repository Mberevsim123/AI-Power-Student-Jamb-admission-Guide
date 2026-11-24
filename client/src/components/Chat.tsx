import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent, UIEvent } from "react";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  Check,
  Copy,
  GraduationCap,
  ListChecks,
  MessageCircle,
  Square,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";

import { api, ApiError, errorMessage, safeUrl } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { AgentState, Briefing, Message, Source } from "../lib/types";
import { ErrorNotice, SourceLink, Spinner } from "./ui";
import { AgentTurn, BriefingPanel, OfficialLinks } from "./Agent";
import { useReveal } from "../lib/reveal";
import { SearchGrounding } from "./SearchGrounding";

const Markdown = lazy(() => import("./Markdown"));

/** Greeting by the student's own clock. */
function greeting(now = new Date()) {
  const hour = now.getHours();
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

const suggestions = [
  {
    icon: GraduationCap,
    title: "Explore my options",
    prompt: "Which universities can I apply to with my JAMB score?",
  },
  {
    icon: BookOpen,
    title: "Subject combinations",
    prompt: "What JAMB subjects do I need for my preferred course?",
  },
  {
    icon: ListChecks,
    title: "Post-UTME screening",
    prompt: "How does Post-UTME screening work?",
  },
  {
    icon: MessageCircle,
    title: "JAMB registration",
    prompt: "How do I register for JAMB?",
  },
];

function messageSources(message: Message) {
  const sources: Source[] = [...message.rag_sources];
  const walk = (value: unknown) => {
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) {
      value.forEach(walk);
      return;
    }
    const obj = value as Record<string, unknown>;
    if (typeof obj.source_url === "string")
      sources.push({
        doc_title: String(
          obj.university || obj.course || "Admissions publication",
        ),
        source_url: obj.source_url,
        cycle_year: Number(obj.year || obj.requirements_year) || undefined,
      });
    Object.values(obj).forEach(walk);
  };
  // The agent block holds plans and options, not evidence the answer cited.
  const { agent: _agent, ...evidence } = message.structured_context;
  void _agent;
  walk(evidence);
  return sources.filter(
    (s, index) =>
      safeUrl(s.source_url) &&
      sources.findIndex((other) => other.source_url === s.source_url) === index,
  );
}

/** Live progress from the agent's real steps while the answer is prepared. */
function ThinkingStatus({ agent }: { agent?: AgentState }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(() => setElapsed(Date.now() - started), 500);
    return () => clearInterval(timer);
  }, []);
  const steps = agent?.actions?.filter((a) => a.tool !== "referral") ?? [];
  const label = !agent
    ? "Reading your question…"
    : elapsed < 3500
      ? "Writing your answer…"
      : "Checking facts against official records…";
  return (
    <div className="thinking" role="status" aria-live="polite">
      {steps.length > 0 && (
        <ul className="thinking-steps">
          {steps.slice(-3).map((step) => (
            <li key={step.summary}>
              <Check size={13} aria-hidden="true" />
              {step.summary}
            </li>
          ))}
        </ul>
      )}
      <Spinner label={label} />
    </div>
  );
}

function MessageCard({
  message,
  busy,
  latest,
  chatBusy,
  onSend,
}: {
  message: Message;
  busy: boolean;
  latest: boolean;
  chatBusy: boolean;
  onSend: (text: string) => void;
}) {
  const agent = message.structured_context.agent as AgentState | undefined;
  const { visible, revealing } = useReveal(
    message.content || "",
    Boolean(message.reveal) && message.role === "assistant",
  );
  const [copied, setCopied] = useState(false);
  const [rating, setRating] = useState<number | null>(null);
  const [ratingBusy, setRatingBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const sources = messageSources(message);
  const isUser = message.role === "user";
  useEffect(() => {
    if (copied) {
      const timer = setTimeout(() => setCopied(false), 2000);
      return () => clearTimeout(timer);
    }
  }, [copied]);
  async function feedback(value: number) {
    setRatingBusy(true);
    setNotice("");
    try {
      await api.post(`/chat/messages/${message.id}/feedback`, {
        rating: value,
      });
      setRating(value);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409)
        setNotice("You’ve already rated this response.");
      else setNotice(errorMessage(error));
    } finally {
      setRatingBusy(false);
    }
  }
  return (
    <article
      className={`message ${isUser ? "message-user" : "message-assistant"}`}
    >
      {!isUser && (
        <span className="assistant-avatar" aria-hidden="true">
          <GraduationCap size={20} />
        </span>
      )}
      <div className="message-body">
        {!isUser && (
          <div className="message-author">
            JAMB Guide <span>YOUR ADMISSIONS COMPANION</span>
          </div>
        )}
        {message.content ? (
          <>
            <div
              className={`markdown${revealing ? " revealing" : ""}`}
              aria-hidden={revealing || undefined}
            >
              <Suspense fallback={<p>{visible}</p>}>
                <Markdown>{visible}</Markdown>
              </Suspense>
            </div>
            {revealing && <div className="sr-only">{message.content}</div>}
          </>
        ) : !isUser && busy ? (
          <ThinkingStatus agent={agent} />
        ) : message.state !== "failed" ? (
          <p className="muted">No response was saved.</p>
        ) : null}
        {message.state === "failed" && (
          <div className="message-failed" role="status">
            {message.error ||
              message.error_message ||
              "This response could not be completed. Please try again."}
          </div>
        )}
        {sources.length > 0 && !revealing && (
          <details className="sources">
            <summary>
              <BookOpen size={14} />
              {sources.length} source{sources.length === 1 ? "" : "s"}{" "}
              referenced
            </summary>
            <div className="source-items">
              {sources.map((source) => (
                <SourceLink key={source.source_url} url={source.source_url}>
                  {source.doc_title}
                  {source.cycle_year ? ` · ${source.cycle_year}` : ""}
                  {source.origin === "web"
                    ? " · Web"
                    : source.origin === "knowledge_base"
                      ? " · Knowledge base"
                      : ""}
                </SourceLink>
              ))}
            </div>
          </details>
        )}
        {!isUser && message.state === "completed" && !revealing && (
          <SearchGrounding value={message.structured_context.web_grounding} />
        )}
        {!isUser && message.content && !busy && !revealing && (
          <div className="message-actions">
            <button
              className="icon-button"
              aria-label={copied ? "Response copied" : "Copy response"}
              title="Copy response"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(message.content || "");
                  setCopied(true);
                  setNotice("");
                } catch {
                  setNotice(
                    "Copy is unavailable here. You can select the response text to copy it.",
                  );
                }
              }}
            >
              {copied ? <Check size={15} /> : <Copy size={15} />}
            </button>
            {message.id > 0 && message.state === "completed" && (
              <>
                <span className="action-divider" />
                <button
                  className={`icon-button ${rating === 1 ? "rated" : ""}`}
                  aria-label="Helpful response"
                  title="Helpful response"
                  disabled={ratingBusy || rating !== null}
                  onClick={() => feedback(1)}
                >
                  <ThumbsUp size={15} />
                </button>
                <button
                  className={`icon-button ${rating === -1 ? "rated" : ""}`}
                  aria-label="Unhelpful response"
                  title="Unhelpful response"
                  disabled={ratingBusy || rating !== null}
                  onClick={() => feedback(-1)}
                >
                  <ThumbsDown size={15} />
                </button>
                {rating !== null && <small>Thanks for your feedback</small>}
              </>
            )}
          </div>
        )}
        {notice && (
          <small role="status" className="muted">
            {notice}
          </small>
        )}
        {!isUser && !revealing && agent?.links?.length ? (
          <OfficialLinks links={agent.links} />
        ) : null}
        {!isUser && agent && message.state !== "failed" && !revealing && (
          <AgentTurn
            state={agent}
            latest={latest && !busy}
            busy={chatBusy}
            onSend={onSend}
          />
        )}
      </div>
    </article>
  );
}

interface ChatProps {
  messages: Message[];
  busy: boolean;
  loading: boolean;
  error: string;
  draft: string;
  setDraft: (draft: string) => void;
  onSend: (message: string) => void;
  onStop: () => void;
}
export function Chat({
  messages,
  busy,
  loading,
  error,
  draft,
  setDraft,
  onSend,
  onStop,
}: ChatProps) {
  const { user } = useAuth();
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const follow = useRef(true);
  const [showScroll, setShowScroll] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  // Keep the newest text in view while an answer is revealed, unless the
  // student has scrolled up to read something else.
  useEffect(() => {
    const list = listRef.current;
    if (!list || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      if (follow.current)
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "instant" });
    });
    observer.observe(list);
    return () => observer.disconnect();
  }, [messages.length > 0]);
  useEffect(() => {
    if (messages.length === 0 && !loading) {
      follow.current = true;
      scrollRef.current?.scrollTo({ top: 0, behavior: "instant" });
      setShowScroll(false);
      return;
    }
    if (follow.current)
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "instant",
      });
  }, [messages, loading]);
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
      inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 150)}px`;
    }
  }, [draft]);
  function trackScroll(event: UIEvent<HTMLDivElement>) {
    const node = event.currentTarget;
    follow.current =
      node.scrollHeight - node.scrollTop - node.clientHeight < 100;
    setShowScroll(!follow.current);
  }
  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!draft.trim() || busy || loading) return;
    follow.current = true;
    onSend(draft.trim());
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  }
  const name = user?.profile?.display_name?.split(" ")[0] || user?.username;
  const idle = messages.length === 0 && !loading && !busy;
  const [personal, setPersonal] = useState<string[]>([]);
  const [recall, setRecall] = useState("");
  const onBriefing = useCallback((briefing: Briefing) => {
    setPersonal(briefing.suggestions);
    const last = briefing.recall;
    setRecall(
      last?.course
        ? last.course + (last.universities[0] ? ` at ${last.universities[0]}` : "")
        : "",
    );
  }, []);
  const lastAssistant = messages.reduce(
    (last, message, index) => (message.role === "assistant" ? index : last),
    -1,
  );
  return (
    <div className="chat-layout">
      <section
        className={`chat-column${idle ? " chat-idle" : ""}`}
        aria-label="Admissions chat"
      >
        <div className="chat-scroll" ref={scrollRef} onScroll={trackScroll}>
          {loading ? (
            <div className="conversation-loading">
              <Spinner label="Opening conversation" />
            </div>
          ) : messages.length === 0 ? (
            <div className="welcome">
              <div className="welcome-intro">
                <p className="welcome-greeting">
                  {greeting()}
                  {name ? ` ${name}` : ""}. Ask anything about JAMB and university
                  admissions.
                </p>
                {recall && (
                  <p className="welcome-recall">
                    Last time we looked at {recall}.{" "}
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => onSend(`Let's continue with ${recall}`)}
                    >
                      Continue
                    </button>
                  </p>
                )}
              </div>
              {user?.data_consent_at && (
                <BriefingPanel onSend={onSend} onBriefing={onBriefing} />
              )}
              <div className="suggestions">
                {(personal.length
                  ? personal.map((prompt, index) => ({
                      ...suggestions[index % suggestions.length],
                      title: prompt,
                      prompt,
                    }))
                  : suggestions
                ).map(({ icon: Icon, title, prompt }) => (
                  <button
                    key={title}
                    className="suggestion"
                    onClick={() => {
                      if (personal.length) onSend(prompt);
                      else {
                        setDraft(prompt);
                        inputRef.current?.focus();
                      }
                    }}
                  >
                    <span className="suggestion-icon">
                      <Icon size={20} strokeWidth={1.6} />
                    </span>
                    <span className="suggestion-text">
                      <strong>{title}</strong>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div
              ref={listRef}
              className="message-list"
              role="log"
              aria-live="polite"
              aria-busy={busy}
              aria-label="Conversation messages"
            >
              {messages.map((message, index) => (
                <MessageCard
                  key={message.client_key ?? message.id}
                  message={message}
                  busy={busy && message.id < 0 && message.role === "assistant"}
                  latest={index === lastAssistant}
                  chatBusy={busy || loading}
                  onSend={onSend}
                />
              ))}
            </div>
          )}
        </div>
        {showScroll && (
          <button
            className="scroll-bottom"
            aria-label="Scroll to latest message"
            onClick={() => {
              follow.current = true;
              scrollRef.current?.scrollTo({
                top: scrollRef.current.scrollHeight,
                behavior: "smooth",
              });
            }}
          >
            <ArrowDown size={17} />
          </button>
        )}
        <div className="composer-area">
          {error &&
            !messages.some(
              (message) =>
                message.role === "assistant" &&
                message.state === "failed" &&
                (message.error || message.error_message) === error,
            ) && <ErrorNotice>{error}</ErrorNotice>}
          <form className="composer" onSubmit={submit}>
            <label className="sr-only" htmlFor="chat-input">
              Ask JAMB Guide
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={keyDown}
              maxLength={2000}
              rows={1}
              placeholder="Ask JAMB Guide…"
              disabled={loading}
            />
            <div className="composer-controls">
              {draft.length > 1800 && <small>{draft.length}/2000</small>}
              {busy ? (
                <button
                  className="send-button stopping"
                  type="button"
                  onClick={onStop}
                  aria-label="Stop response"
                >
                  <Square size={14} fill="currentColor" />
                </button>
              ) : (
                <button
                  className="send-button"
                  disabled={!draft.trim() || loading}
                  aria-label="Send message"
                >
                  <ArrowUp size={20} />
                </button>
              )}
            </div>
          </form>
          <p className="composer-disclaimer">
            Confirm admission details with the institution.
          </p>
        </div>
      </section>
    </div>
  );
}
