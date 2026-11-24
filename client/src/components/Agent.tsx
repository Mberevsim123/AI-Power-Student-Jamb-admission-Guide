import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  BellRing,
  Check,
  CircleCheck,
  Circle,
  ExternalLink,
  Landmark,
  Route,
  Save,
  Sparkles,
} from "lucide-react";
import { api, errorMessage, safeUrl } from "../lib/api";
import { useAuth } from "../lib/auth";
import type {
  AgentState,
  Briefing,
  Journey,
  Nudge,
  OfficialLink,
} from "../lib/types";

const FIELD_LABELS: Record<string, string> = {
  jamb_score: "UTME score",
  preferred_course: "Course",
  preferred_universities: "Target schools",
  olevel_results: "O'Level grades",
  state_of_origin: "State of origin",
  academic_stage: "Stage",
};

function showValue(value: unknown) {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object")
    return Object.entries(value as Record<string, string>)
      .map(([subject, grade]) => `${subject} ${grade}`)
      .join(", ");
  return String(value).replace(/_/g, " ");
}

/** Where to act: official platforms and institution sites for this answer. */
export function OfficialLinks({ links }: { links: OfficialLink[] }) {
  const safe = links.filter((link) => safeUrl(link.url));
  if (!safe.length) return null;
  return (
    <nav className="official-links" aria-label="Official links">
      <span className="official-links-title">
        <Landmark size={14} aria-hidden="true" /> Go to the official site
      </span>
      <div className="official-links-list">
        {safe.map((link) => (
          <a
            key={link.url}
            href={safeUrl(link.url)}
            target="_blank"
            rel="noopener noreferrer"
            className="official-link"
            title={link.purpose}
          >
            <span className="official-link-text">
              <strong>{link.label}</strong>
              <small>{new URL(link.url).hostname.replace(/^www\./, "")}</small>
            </span>
            <ExternalLink size={14} aria-hidden="true" />
            <span className="sr-only"> (opens in a new tab)</span>
          </a>
        ))}
      </div>
    </nav>
  );
}

export function NudgeList({ nudges }: { nudges: Nudge[] }) {
  if (!nudges.length) return null;
  return (
    <div className="agent-nudges">
      {nudges.map((nudge) => (
        <div key={nudge.title} className={`agent-nudge ${nudge.severity}`}>
          <BellRing size={15} />
          <span>
            <strong>{nudge.title}</strong> — {nudge.detail}
          </span>
          {safeUrl(nudge.source_url) && (
            <a
              href={safeUrl(nudge.source_url)}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Source for ${nudge.title}`}
            >
              <ArrowUpRight size={14} />
            </a>
          )}
        </div>
      ))}
    </div>
  );
}

function ProfileProposal({ patch }: { patch: Record<string, unknown> }) {
  const { loadUser } = useAuth();
  const [state, setState] = useState<"idle" | "saving" | "saved" | "dismissed">(
    "idle",
  );
  const [error, setError] = useState("");
  const entries = Object.entries(patch);
  if (!entries.length || state === "dismissed") return null;
  async function save() {
    setState("saving");
    setError("");
    try {
      await api.patch("/admissions/profile", patch);
      await loadUser();
      setState("saved");
    } catch (err) {
      setError(errorMessage(err));
      setState("idle");
    }
  }
  return (
    <div className="agent-card agent-proposal">
      <div className="agent-card-head">
        <Save size={15} />
        {state === "saved" ? "Saved to your profile" : "Remember this for next time?"}
      </div>
      <dl>
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt>{FIELD_LABELS[key] || key}</dt>
            <dd>{showValue(value)}</dd>
          </div>
        ))}
      </dl>
      {error && <small className="agent-error">{error}</small>}
      {state !== "saved" && (
        <div className="agent-card-actions">
          <button
            className="button small primary"
            onClick={save}
            disabled={state === "saving"}
          >
            {state === "saving" ? "Saving…" : "Save to profile"}
          </button>
          <button className="text-button" onClick={() => setState("dismissed")}>
            Not now
          </button>
        </div>
      )}
    </div>
  );
}

/** The agent's contribution to one assistant turn. */
export function AgentTurn({
  state,
  latest,
  busy,
  onSend,
}: {
  state: AgentState;
  latest: boolean;
  busy: boolean;
  onSend: (text: string) => void;
}) {
  const options = state.options || [];
  return (
    <div className="agent-turn">
      {state.actions?.length > 0 && (
        <details className="agent-actions">
          <summary>
            <Sparkles size={13} />
            {state.actions.length} step{state.actions.length === 1 ? "" : "s"} taken
          </summary>
          <ul>
            {state.actions.map((action) => (
              <li key={action.summary}>{action.summary}</li>
            ))}
          </ul>
        </details>
      )}
      {latest && options.length > 0 && (
        <div className="agent-options" role="group" aria-label="Options to consider">
          {options.map((option) => (
            <button
              key={option.id}
              className={`agent-option ${option.kind}`}
              disabled={busy}
              onClick={() => onSend(`Option ${option.id}`)}
            >
              <span className="agent-option-id">{option.id}</span>
              <span className="agent-option-label">{option.label}</span>
              {typeof option.margin === "number" && (
                <span
                  className={`agent-margin ${option.margin >= 10 ? "good" : option.margin >= 0 ? "tight" : "short"}`}
                >
                  {option.margin >= 0 ? `+${option.margin}` : option.margin}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
      {state.profile_patch && Object.keys(state.profile_patch).length > 0 && (
        <ProfileProposal patch={state.profile_patch} />
      )}
      {latest && <NudgeList nudges={state.nudges || []} />}
      {latest && state.suggestions?.length > 0 && (
        <div className="agent-suggestions" aria-label="Suggested replies">
          {state.suggestions.map((prompt) => (
            <button
              key={prompt}
              className="agent-chip"
              disabled={busy}
              onClick={() => onSend(prompt)}
            >
              {prompt}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function JourneyCard({
  journey,
  onChange,
  onSend,
}: {
  journey: Journey;
  onChange: (journey: Journey) => void;
  onSend: (text: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState("");
  const percent = Math.round((100 * journey.completed) / journey.total);
  async function toggle(id: string, done: boolean) {
    setPending(id);
    try {
      onChange(await api.patch<Journey>(`/agent/journey/${id}`, { done }));
    } finally {
      setPending("");
    }
  }
  return (
    <section className="agent-card agent-journey" aria-label="Your admission journey">
      <div className="agent-card-head">
        <Route size={15} /> Your admission journey
        <span className="agent-progress-label">
          {journey.completed}/{journey.total}
        </span>
      </div>
      <div className="agent-progress" aria-hidden="true">
        <span style={{ width: `${percent}%` }} />
      </div>
      {journey.next && (
        <div className="agent-next">
          <div>
            <small>Next step</small>
            <strong>{journey.next.title}</strong>
            <p>{journey.next.hint}</p>
          </div>
          <div className="agent-card-actions">
            <button
              className="button small primary"
              onClick={() => onSend(journey.next!.prompt)}
            >
              Help me with this
            </button>
            <button
              className="text-button"
              disabled={pending === journey.next.id}
              onClick={() => toggle(journey.next!.id, true)}
            >
              <Check size={14} /> Mark done
            </button>
          </div>
        </div>
      )}
      <button className="text-button agent-toggle" onClick={() => setOpen(!open)}>
        {open ? "Hide all steps" : "See all steps"}
      </button>
      {open && (
        <ul className="agent-steps">
          {journey.steps.map((step) => (
            <li key={step.id} className={step.done ? "done" : ""}>
              <button
                aria-label={`${step.title}: ${step.done ? "done" : "not done"}`}
                disabled={pending === step.id}
                onClick={() => toggle(step.id, !step.done)}
              >
                {step.done ? <CircleCheck size={16} /> : <Circle size={16} />}
              </button>
              <span>{step.title}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Proactive start-of-session briefing: the agent speaks first. */
export function BriefingPanel({
  onSend,
  onSuggest,
}: {
  onSend: (text: string) => void;
  onSuggest: (prompts: string[]) => void;
}) {
  const [briefing, setBriefing] = useState<Briefing | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    api
      .get<Briefing>("/agent/briefing", controller.signal)
      .then((value) => {
        setBriefing(value);
        onSuggest(value.suggestions);
      })
      .catch(() => {
        /* The static welcome remains useful if the briefing is unavailable. */
      });
    return () => controller.abort();
  }, [onSuggest]);
  if (!briefing) return null;
  const gap = briefing.profile.missing[0];
  return (
    <div className="agent-briefing">
      {briefing.recall && (
        <p className="agent-recall">
          Last time we looked at <strong>{briefing.recall.course}</strong>
          {briefing.recall.universities[0]
            ? ` at ${briefing.recall.universities[0]}`
            : ""}
          .{" "}
          <button
            className="text-button"
            onClick={() =>
              onSend(
                `Let's continue with ${briefing.recall!.course}${briefing.recall!.universities[0] ? ` at ${briefing.recall!.universities[0]}` : ""}`,
              )
            }
          >
            Pick up where we left off
          </button>
        </p>
      )}
      <NudgeList nudges={briefing.nudges} />
      <JourneyCard
        journey={briefing.journey}
        onSend={onSend}
        onChange={(journey) => setBriefing({ ...briefing, journey })}
      />
      {gap && briefing.profile.completeness < 70 && (
        <p className="agent-gap">
          Tip: tell me {gap.label} — e.g. “{gap.starter}” — and I’ll personalise
          every answer.
        </p>
      )}
    </div>
  );
}
