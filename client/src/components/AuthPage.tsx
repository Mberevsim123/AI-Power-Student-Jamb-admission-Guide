import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { ArrowLeft, Eye, EyeOff, GraduationCap, LoaderCircle } from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";

export type AuthMode = "login" | "register";

/** Password input with an eye toggle (open eye = show, closed eye = hide). */
function PasswordField({
  label,
  autoComplete,
  minLength,
  hint,
}: {
  label: string;
  autoComplete: string;
  minLength: number;
  hint?: string;
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  const [capsLock, setCapsLock] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  function trackCaps(event: KeyboardEvent<HTMLInputElement>) {
    setCapsLock(event.getModifierState?.("CapsLock") ?? false);
  }
  const describedBy = [hint && `${id}-hint`, capsLock && `${id}-caps`]
    .filter(Boolean)
    .join(" ");
  return (
    <div className="auth-field">
      <label htmlFor={id}>{label}</label>
      <div className="auth-password">
        <input
          ref={input}
          id={id}
          name="password"
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          minLength={minLength}
          maxLength={72}
          required
          spellCheck={false}
          autoCapitalize="none"
          aria-describedby={describedBy || undefined}
          onKeyDown={trackCaps}
          onKeyUp={trackCaps}
          onBlur={() => setCapsLock(false)}
        />
        <button
          type="button"
          className="auth-eye"
          aria-label={visible ? "Hide password" : "Show password"}
          aria-pressed={visible}
          aria-controls={id}
          title={visible ? "Hide password" : "Show password"}
          // Keep focus (and the caret) in the field while toggling.
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            setVisible((v) => !v);
            input.current?.focus();
          }}
        >
          {visible ? <EyeOff size={19} /> : <Eye size={19} />}
        </button>
      </div>
      {hint && (
        <small id={`${id}-hint`} className="auth-hint">
          {hint}
        </small>
      )}
      {capsLock && (
        <small id={`${id}-caps`} className="auth-warning" role="status">
          Caps Lock is on
        </small>
      )}
    </div>
  );
}

function Field({
  label,
  ...props
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  const id = useId();
  return (
    <div className="auth-field">
      <label htmlFor={id}>{label}</label>
      <input id={id} {...props} />
    </div>
  );
}

/** Full-page sign-in, account creation and consent screen. */
export function AuthPage({
  mode,
  onModeChange,
  onClose,
  consentOnly = false,
  notice,
}: {
  mode: AuthMode;
  onModeChange: (mode: AuthMode) => void;
  onClose: () => void;
  consentOnly?: boolean;
  notice?: string;
}) {
  const { login, loadUser, logout } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
    setError("");
  }, [mode, consentOnly]);
  useEffect(() => {
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && !pending) onClose();
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [onClose, pending]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError("");
    try {
      if (consentOnly) {
        await api.post("/auth/consent", { data_consent: true });
        await loadUser();
      } else {
        const email = String(form.get("email")).trim();
        const password = String(form.get("password"));
        if (mode === "register")
          await api.post("/auth/register", {
            email,
            password,
            username: String(form.get("username")).trim(),
            data_consent: true,
          });
        await login(email, password);
      }
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setPending(false);
    }
  }

  const title = consentOnly
    ? "Before you continue"
    : mode === "login"
      ? "Sign in to JAMB Guide"
      : "Create your account";
  const subtitle = consentOnly
    ? "Please review how your information is used."
    : mode === "login"
      ? "Welcome back. Pick up your admission plan where you left off."
      : "Save your progress and get guidance that remembers your goals.";

  return (
    <div className="auth-page" role="main" aria-labelledby="auth-title">
      <header className="auth-top">
        <span className="auth-brand">
          <GraduationCap size={22} strokeWidth={1.8} aria-hidden="true" />
          JAMB Guide
        </span>
        <button type="button" className="auth-back" onClick={onClose} disabled={pending}>
          <ArrowLeft size={16} aria-hidden="true" />
          {consentOnly ? "Not now" : "Continue without an account"}
        </button>
      </header>

      <div className="auth-body">
        <h1 id="auth-title" ref={heading} tabIndex={-1}>
          {title}
        </h1>
        <p className="auth-subtitle">{subtitle}</p>
        {notice && (
          <p className="auth-notice" role="status">
            {notice}
          </p>
        )}

        <form onSubmit={submit} noValidate={false}>
          {!consentOnly && mode === "register" && (
            <Field
              label="Username"
              name="username"
              autoComplete="username"
              minLength={3}
              maxLength={50}
              required
              autoCapitalize="none"
              spellCheck={false}
            />
          )}
          {!consentOnly && (
            <Field
              label="Email address"
              name="email"
              type="email"
              autoComplete="email"
              inputMode="email"
              required
              autoCapitalize="none"
              spellCheck={false}
            />
          )}
          {!consentOnly && (
            <PasswordField
              key={mode}
              label="Password"
              autoComplete={mode === "register" ? "new-password" : "current-password"}
              minLength={mode === "register" ? 8 : 1}
              hint={mode === "register" ? "Use at least 8 characters." : undefined}
            />
          )}
          {(consentOnly || mode === "register") && (
            <label className="auth-consent">
              <input type="checkbox" name="consent" required />
              <span>
                I agree to JAMB Guide storing my account, profile and conversations, and
                sharing my chat context with its AI providers to generate guidance,
                including public web searches when needed.
              </span>
            </label>
          )}
          {error && (
            <p className="auth-error" role="alert">
              {error}
            </p>
          )}
          <button className="auth-submit" disabled={pending}>
            {pending ? (
              <>
                <LoaderCircle size={17} className="spin" aria-hidden="true" />
                Please wait…
              </>
            ) : consentOnly ? (
              "Agree and continue"
            ) : mode === "login" ? (
              "Sign in"
            ) : (
              "Create account"
            )}
          </button>
        </form>

        {consentOnly ? (
          <p className="auth-switch">
            Prefer not to?{" "}
            <button type="button" onClick={() => void logout().then(onClose)}>
              Sign out
            </button>
          </p>
        ) : (
          <p className="auth-switch">
            {mode === "login" ? "New to JAMB Guide?" : "Already have an account?"}{" "}
            <button
              type="button"
              onClick={() => onModeChange(mode === "login" ? "register" : "login")}
            >
              {mode === "login" ? "Create an account" : "Sign in"}
            </button>
          </p>
        )}
        <p className="auth-fineprint">
          Your session stays in this browser tab. Sign out on shared devices. You can
          delete your account and stored data at any time from your profile.
        </p>
      </div>
    </div>
  );
}
