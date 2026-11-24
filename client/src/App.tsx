import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  ArrowRight,
  BookOpen,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  GraduationCap,
  ListChecks,
  LogIn,
  LogOut,
  Menu,
  MessageCircle,
  Plus,
  Search,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { api, errorMessage, streamChat } from "./lib/api";
import { useAuth } from "./lib/auth";
import type {
  Conversation,
  ConversationDetail,
  Message,
  View,
} from "./lib/types";
import { AuthPage } from "./components/AuthPage";
import type { AuthMode } from "./components/AuthPage";
import { AppearanceControl } from "./components/AppearanceControl";
import { Chat } from "./components/Chat";
import { Brand, ErrorNotice, Modal, Spinner } from "./components/ui";
const EligibilityView = lazy(() =>
  import("./views/Eligibility").then((module) => ({
    default: module.EligibilityView,
  })),
);
const UniversityView = lazy(() =>
  import("./views/Universities").then((module) => ({
    default: module.UniversityView,
  })),
);
const ProfileView = lazy(() =>
  import("./views/Profile").then((module) => ({ default: module.ProfileView })),
);

const nav = [
  { id: "chat", label: "Admissions chat", icon: MessageCircle },
  { id: "universities", label: "Universities", icon: GraduationCap },
  { id: "eligibility", label: "Eligibility check", icon: ListChecks },
  { id: "profile", label: "My profile", icon: UserRound },
] as const;

/** The saved reply replaces the live one without remounting, so its reveal continues. */
function withLiveReply(messages: Message[], key: string) {
  const last = messages.map((m) => m.role).lastIndexOf("assistant");
  return messages.map((m, i) =>
    i === last ? { ...m, client_key: key, reveal: true } : m,
  );
}

export default function App() {
  const { user, loading: authLoading, logout } = useAuth();
  const [view, setView] = useState<View>("chat");
  const [menuOpen, setMenuOpen] = useState(false);
  // Sign-in is a page with its own address (#login / #register): refresh and Back work.
  const authFromHash = (): AuthMode | null =>
    window.location.hash === "#register"
      ? "register"
      : window.location.hash === "#login"
        ? "login"
        : null;
  const [authMode, setAuthMode] = useState<AuthMode | null>(authFromHash);
  const setAuthOpen = useCallback((open: boolean, mode: AuthMode = "login") => {
    if (open) {
      if (window.location.hash !== `#${mode}`) window.location.hash = mode;
      setAuthMode(mode);
    } else {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      setAuthMode(null);
    }
  }, []);
  useEffect(() => {
    const sync = () => setAuthMode(authFromHash());
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  const [helpOpen, setHelpOpen] = useState(false);
  const [online, setOnline] = useState<"checking" | "online" | "offline">(
    "checking",
  );
  const [history, setHistory] = useState<Conversation[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [historySearch, setHistorySearch] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [chatLoading, setChatLoading] = useState(false);
  const [chatError, setChatError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Conversation | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [sessionNotice, setSessionNotice] = useState("");
  const abort = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);
  const inFlight = useRef(false);
  const userId = user?.id;
  const activeUser = useRef(userId);
  activeUser.current = userId;

  const checkHealth = useCallback(() => {
    setOnline("checking");
    api
      .health()
      .then(() => setOnline("online"))
      .catch(() => setOnline("offline"));
  }, []);
  useEffect(checkHealth, [checkHealth]);
  useEffect(() => {
    const focus = () => checkHealth();
    window.addEventListener("online", focus);
    return () => window.removeEventListener("online", focus);
  }, [checkHealth]);

  const refreshHistory = useCallback(
    async (offset = 0) => {
      if (!userId) return;
      try {
        const result = await api.get<Conversation[]>(
          `/chat/conversations?limit=20&offset=${offset}`,
        );
        if (activeUser.current !== userId) return;
        setHistory((old) => (offset ? [...old, ...result] : result));
        setHasMore(result.length === 20);
        setHistoryError("");
      } catch (error) {
        if (activeUser.current === userId) setHistoryError(errorMessage(error));
      }
    },
    [userId],
  );

  useEffect(() => {
    abort.current?.abort();
    requestVersion.current += 1;
    inFlight.current = false;
    setBusy(false);
    setChatLoading(false);
    setMessages([]);
    setConversationId(null);
    setHistory([]);
    setHistoryError("");
    setHasMore(false);
    setDeleteTarget(null);
    setChatError("");
    if (userId && user?.data_consent_at) void refreshHistory();
  }, [userId, user?.data_consent_at, refreshHistory]);
  useEffect(() => {
    const expired = () => {
      setSessionNotice("Your session has expired. Sign in again to continue.");
      setAuthOpen(true);
    };
    window.addEventListener("jamb:session-expired", expired);
    return () => {
      abort.current?.abort();
      window.removeEventListener("jamb:session-expired", expired);
    };
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", dismiss);
    return () => window.removeEventListener("keydown", dismiss);
  }, [menuOpen]);

  function requireUser() {
    if (!user || !user.data_consent_at) {
      setAuthOpen(true);
      return false;
    }
    return true;
  }
  function navigate(next: View) {
    setView(next);
    setMenuOpen(false);
  }
  function newChat() {
    if (inFlight.current) return;
    requestVersion.current += 1;
    setConversationId(null);
    setMessages([]);
    setChatError("");
    setChatLoading(false);
    setDraft("");
    navigate("chat");
  }
  async function selectConversation(id: number) {
    if (inFlight.current) return;
    const version = ++requestVersion.current;
    setChatLoading(true);
    setChatError("");
    navigate("chat");
    try {
      const detail = await api.get<ConversationDetail>(
        `/chat/conversations/${id}`,
      );
      if (version !== requestVersion.current) return;
      setMessages(detail.messages);
      setConversationId(id);
    } catch (error) {
      if (version === requestVersion.current) setChatError(errorMessage(error));
    } finally {
      if (version === requestVersion.current) setChatLoading(false);
    }
  }
  async function sendMessage(text: string) {
    if (inFlight.current || chatLoading || !text.trim() || !requireUser())
      return;
    text = text.trim();
    inFlight.current = true;
    setBusy(true);
    setChatError("");
    setDraft("");
    const version = requestVersion.current;
    const tempId = -Date.now();
    const assistantId = tempId - 1;
    const temp: Message = {
      id: tempId,
      conversation_id: conversationId || 0,
      role: "user",
      state: "completed",
      content: text,
      rag_sources: [],
      structured_context: {},
      created_at: new Date().toISOString(),
    };
    setMessages((old) => [
      ...old,
      temp,
      {
        ...temp,
        id: assistantId,
        role: "assistant",
        content: "",
        state: "streaming",
        client_key: `live${assistantId}`,
        reveal: true,
      },
    ]);
    const controller = new AbortController();
    abort.current = controller;
    let currentId = conversationId;
    try {
      await streamChat(
        text,
        conversationId,
        controller.signal,
        (metadata) => {
          currentId = metadata.conversation_id;
          if (version === requestVersion.current) setConversationId(currentId);
        },
        (delta) => {
          if (version === requestVersion.current)
            setMessages((old) =>
              old.map((message) =>
                message.id === assistantId
                  ? { ...message, content: (message.content || "") + delta }
                  : message,
              ),
            );
        },
        (agent) => {
          // The agent's plan arrives before the answer: show its steps while it writes.
          if (version === requestVersion.current)
            setMessages((old) =>
              old.map((message) =>
                message.id === assistantId
                  ? {
                      ...message,
                      structured_context: { ...message.structured_context, agent },
                    }
                  : message,
              ),
            );
        },
      );
      if (currentId && version === requestVersion.current) {
        const detail = await api.get<ConversationDetail>(
          `/chat/conversations/${currentId}`,
        );
        if (version === requestVersion.current)
          setMessages(withLiveReply(detail.messages, `live${assistantId}`));
      }
      setOnline("online");
    } catch (error) {
      if (version !== requestVersion.current) return;
      const message = controller.signal.aborted
        ? "Response stopped. Reopen this conversation to see its saved state."
        : errorMessage(error);
      setMessages((old) =>
        old.map((item) =>
          item.id === assistantId
            ? { ...item, state: "failed", error: message }
            : item,
        ),
      );
      if (!controller.signal.aborted) setChatError(message);
      if (currentId && !controller.signal.aborted) {
        try {
          const detail = await api.get<ConversationDetail>(
            `/chat/conversations/${currentId}`,
          );
          if (version === requestVersion.current) setMessages(detail.messages);
        } catch {
          // Keep the local failed turn visible if saved history is unavailable.
        }
      }
    } finally {
      if (version === requestVersion.current) {
        setBusy(false);
        inFlight.current = false;
        void refreshHistory();
      }
    }
  }
  async function removeConversation() {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    setDeleteError("");
    try {
      await api.delete(`/chat/conversations/${deleteTarget.id}`);
      setHistory((old) => old.filter((item) => item.id !== deleteTarget.id));
      if (conversationId === deleteTarget.id) newChat();
      setDeleteTarget(null);
    } catch (error) {
      setDeleteError(errorMessage(error));
    } finally {
      setDeleteBusy(false);
    }
  }
  async function signOut() {
    abort.current?.abort();
    try {
      await logout();
      setDraft("");
      setSessionNotice("");
    } catch {
      setSessionNotice(
        "Signed out on this device. The server could not confirm session revocation.",
      );
    }
  }
  const title = nav.find((item) => item.id === view)?.label;
  const activeTitle = history.find((item) => item.id === conversationId)?.title;
  const visibleHistory = history.filter((item) =>
    item.title.toLowerCase().includes(historySearch.toLowerCase()),
  );
  const consentOnly = Boolean(user && !user.data_consent_at);
  if (authMode && (!user || consentOnly))
    return (
      <AuthPage
        mode={authMode}
        consentOnly={consentOnly}
        notice={sessionNotice}
        onModeChange={(mode) => {
          window.history.replaceState(null, "", `#${mode}`);
          setAuthMode(mode);
        }}
        onClose={() => {
          setAuthOpen(false);
          setSessionNotice("");
        }}
      />
    );
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      {menuOpen && (
        <button
          className="sidebar-scrim"
          aria-label="Dismiss navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        className={`sidebar ${menuOpen ? "sidebar-open" : ""}`}
        aria-label="Main navigation"
      >
        <div className="sidebar-brand">
          <Brand />
          <button
            className="icon-button mobile-close"
            aria-label="Close navigation"
            onClick={() => setMenuOpen(false)}
          >
            <X size={20} />
          </button>
        </div>
        <button className="new-chat" onClick={newChat} disabled={busy}>
          <Plus size={18} />
          New chat
        </button>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          {nav.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={`nav-item ${view === id ? "active" : ""}`}
              aria-current={view === id ? "page" : undefined}
              onClick={() => navigate(id)}
            >
              <Icon size={18} strokeWidth={1.65} />
              <span>{label}</span>
              {view === id && <span className="nav-active-dot" />}
            </button>
          ))}
        </nav>
        <div className="history-section">
          <div className="history-heading">
            <span className="nav-label">RECENT</span>
            {history.length > 0 && <Search size={13} />}
          </div>
          {history.length > 5 && (
            <label className="sr-only-input">
              <span className="sr-only">Search conversations</span>
              <input
                value={historySearch}
                onChange={(e) => setHistorySearch(e.target.value)}
                placeholder="Search your conversations"
              />
            </label>
          )}
          {authLoading ? (
            <Spinner label="Loading account" />
          ) : !user ? (
            <p className="history-placeholder">
              <button onClick={() => setAuthOpen(true)}>
                Sign in to save chats <ArrowRight size={12} />
              </button>
            </p>
          ) : history.length === 0 ? (
            <p className="history-placeholder">
              Your chats will appear here.
            </p>
          ) : (
            <div className="history-list">
              {visibleHistory.map((item) => (
                <div
                  key={item.id}
                  className={`history-item ${conversationId === item.id ? "current" : ""}`}
                >
                  <button
                    disabled={busy}
                    title={item.title}
                    onClick={() => selectConversation(item.id)}
                  >
                    <MessageCircle size={14} />
                    <span>{item.title}</span>
                  </button>
                  <button
                    className="history-delete"
                    disabled={busy}
                    aria-label={`Delete conversation: ${item.title}`}
                    onClick={() => {
                      setDeleteTarget(item);
                      setDeleteError("");
                    }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
              {visibleHistory.length === 0 && (
                <small className="muted">No matching conversations.</small>
              )}
              {hasMore && (
                <button
                  className="load-more"
                  onClick={() => refreshHistory(history.length)}
                >
                  Load more <ChevronDown size={13} />
                </button>
              )}
            </div>
          )}
          {historyError && (
            <button className="history-retry" onClick={() => refreshHistory()}>
              Couldn’t load history. Try again.
            </button>
          )}
        </div>
        <div className="sidebar-bottom">
          <button className="help-link" onClick={() => setHelpOpen(true)}>
            <CircleHelp size={17} /> Help
            <ChevronRight size={14} />
          </button>
          <div className="account-row">
            <span className="account-avatar">
              {user ? (
                user.username.slice(0, 2).toUpperCase()
              ) : (
                <UserRound size={17} />
              )}
            </span>
            <button
              className="account-details"
              onClick={() => (user ? navigate("profile") : setAuthOpen(true))}
            >
              <strong>
                {user?.profile?.display_name ||
                  user?.username ||
                  "Student account"}
              </strong>
              <small>
                {user ? "Manage profile" : "Sign in to get started"}
              </small>
            </button>
            <button
              className="icon-button"
              aria-label={user ? "Sign out" : "Sign in"}
              onClick={() => (user ? void signOut() : setAuthOpen(true))}
            >
              {user ? <LogOut size={16} /> : <LogIn size={16} />}
            </button>
          </div>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Open navigation"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen(true)}
            >
              <Menu size={21} />
            </button>
            <span className="breadcrumb-root">Workspace</span>
            <ChevronRight size={13} />
            <span>{title}</span>
          </div>
          <div className="topbar-actions">
            <AppearanceControl />
            {user ? (
              <button
                className="topbar-profile"
                onClick={() => navigate("profile")}
              >
                <span className="avatar-small">
                  {user.username.slice(0, 1).toUpperCase()}
                </span>
                <span>{user.profile?.display_name || user.username}</span>
                <ChevronDown size={13} />
              </button>
            ) : (
              <>
                <button
                  className="button small secondary"
                  onClick={() => setAuthOpen(true, "login")}
                >
                  Sign in
                </button>
                <button
                  className="button small primary"
                  onClick={() => setAuthOpen(true, "register")}
                >
                  Create account
                  <ArrowRight size={15} />
                </button>
              </>
            )}
          </div>
        </header>
        <main
          id="main-content"
          className={`main-content ${view === "chat" ? "main-chat" : ""}`}
        >
          {sessionNotice && (
            <div className="global-notice" role="status">
              {sessionNotice}
              <button
                className="icon-button"
                aria-label="Dismiss notice"
                onClick={() => setSessionNotice("")}
              >
                <X size={15} />
              </button>
            </div>
          )}
          <div className="workspace-bar">
            <div>
              <span className="workspace-indicator" />
              <span>
                {view === "chat"
                  ? activeTitle || "New chat"
                  : title}
              </span>
            </div>
            {online !== "online" && (
              <button
                className={`connection-status ${online}`}
                onClick={checkHealth}
                title="Check service connection"
              >
                <span />
                {online === "checking" ? "Connecting…" : "Offline — reconnect"}
              </button>
            )}
          </div>
          <Suspense
            fallback={
              <div className="conversation-loading">
                <Spinner label="Opening workspace" />
              </div>
            }
          >
            {view === "chat" ? (
              <Chat
                messages={messages}
                busy={busy}
                loading={chatLoading}
                error={chatError}
                draft={draft}
                setDraft={setDraft}
                onSend={sendMessage}
                onStop={() => abort.current?.abort()}
              />
            ) : view === "universities" ? (
              <UniversityView
                onAsk={(prompt) => {
                  newChat();
                  setDraft(prompt);
                }}
              />
            ) : view === "eligibility" ? (
              <EligibilityView
                key={user?.id || "guest"}
                onSignIn={() => setAuthOpen(true)}
              />
            ) : (
              <ProfileView
                key={user?.id || "guest"}
                onSignIn={() => setAuthOpen(true)}
              />
            )}
          </Suspense>
        </main>
      </div>
      {deleteTarget && (
        <Modal
          title="Remove this conversation?"
          onClose={() => {
            if (!deleteBusy) setDeleteTarget(null);
          }}
        >
          <div className="modal-content">
            <p>
              “{deleteTarget.title}” will be removed from your conversation
              history. This does not delete your account or other conversations.
            </p>
            {deleteError && <ErrorNotice>{deleteError}</ErrorNotice>}
            <div className="modal-actions">
              <button
                className="button secondary"
                onClick={() => setDeleteTarget(null)}
                disabled={deleteBusy}
              >
                Keep conversation
              </button>
              <button
                className="button danger"
                onClick={removeConversation}
                disabled={deleteBusy}
              >
                {deleteBusy ? "Removing…" : "Remove conversation"}
              </button>
            </div>
          </div>
        </Modal>
      )}
      {helpOpen && (
        <Modal title="A good place to start" onClose={() => setHelpOpen(false)}>
          <div className="modal-content help-content">
            <span className="help-hero">
              <BookOpen size={30} />
            </span>
            <p>
              Think of JAMB Guide as a companion for the questions between you
              and your university goals.
            </p>
            <ol>
              <li>
                <strong>Ask in your own words.</strong> Include the course,
                university and admission year when you can.
              </li>
              <li>
                <strong>Make it personal.</strong> Add your score and preferred
                course in My profile.
              </li>
              <li>
                <strong>Check the sources.</strong> Open source references to
                confirm publication details.
              </li>
            </ol>
            <p className="muted">
              If information hasn’t been published here, we’ll say so.
              Eligibility guidance supports your decisions and does not
              guarantee admission.
            </p>
            <button
              className="button primary full"
              onClick={() => setHelpOpen(false)}
            >
              Let’s get started
              <ArrowRight size={16} />
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
