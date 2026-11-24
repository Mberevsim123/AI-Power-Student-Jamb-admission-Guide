import { useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowRight,
  Check,
  LogIn,
  Save,
  ShieldCheck,
  Sparkles,
  Trash2,
  UserRound,
} from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Profile } from "../lib/types";
import { ErrorNotice, Modal, PageHeading, Spinner } from "../components/ui";

export function ProfileView({ onSignIn }: { onSignIn: () => void }) {
  const { user, loadUser, clearSession } = useAuth();
  const [pending, setPending] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [confirmation, setConfirmation] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [deleting, setDeleting] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user?.data_consent_at) {
      onSignIn();
      return;
    }
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError("");
    setSaved(false);
    try {
      await api.patch<Profile>("/admissions/profile", {
        display_name: form.get("display_name") || null,
        jamb_score:
          form.get("jamb_score") !== "" ? Number(form.get("jamb_score")) : null,
        jamb_year: form.get("jamb_year") ? Number(form.get("jamb_year")) : null,
        academic_stage: form.get("academic_stage"),
        preferred_course: form.get("preferred_course") || null,
        preferred_universities: String(form.get("preferred_universities") || "")
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        state_of_origin: form.get("state_of_origin") || null,
      });
      await loadUser();
      setSaved(true);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setPending(false);
    }
  }
  async function deleteAccount(event: FormEvent) {
    event.preventDefault();
    if (confirmation !== "DELETE") return;
    setDeleting(true);
    setDeleteError("");
    try {
      await api.delete("/auth/me");
      clearSession();
      setDeleteOpen(false);
    } catch (error) {
      setDeleteError(errorMessage(error));
    } finally {
      setDeleting(false);
    }
  }
  return (
    <div className="page-container profile-page">
      <PageHeading
        title="My profile"
      >
        Your details personalise every answer.
      </PageHeading>
      {!user ? (
        <div className="panel profile-sign-in">
          <span className="empty-icon">
            <UserRound size={30} />
          </span>
          <h2>A space for your ambitions</h2>
          <p>
            Sign in to save your study preferences, personalise your guidance,
            and pick up conversations where you left off.
          </p>
          <button className="button primary" onClick={onSignIn}>
            Sign in to continue
            <LogIn size={17} />
          </button>
        </div>
      ) : (
        <div className="profile-layout">
          <form
            className="panel profile-form"
            onSubmit={submit}
            onChange={() => setSaved(false)}
          >
            <div className="profile-summary">
              <span className="large-avatar">
                {user.username.slice(0, 2).toUpperCase()}
              </span>
              <div>
                <h2>{user.profile?.display_name || user.username}</h2>
                <p>{user.email}</p>
              </div>
            </div>
            <div className="section-heading bordered">
              <div>
                <h2>The basics</h2>
              </div>
            </div>
            <div className="field-grid">
              <label>
                Display name
                <input
                  name="display_name"
                  maxLength={100}
                  defaultValue={user.profile?.display_name || ""}
                  placeholder="Your name"
                />
              </label>
              <label>
                State of origin
                <input
                  name="state_of_origin"
                  maxLength={50}
                  defaultValue={user.profile?.state_of_origin || ""}
                  placeholder="e.g. Nasarawa"
                />
              </label>
            </div>
            <label>
              Where are you in your journey?
              <select
                name="academic_stage"
                defaultValue={user.profile?.academic_stage || "pre_jamb"}
              >
                <option value="pre_jamb">Preparing for JAMB</option>
                <option value="post_jamb">I have my UTME results</option>
                <option value="post_utme">Preparing for Post-UTME</option>
                <option value="direct_entry">
                  Applying through Direct Entry
                </option>
                <option value="admitted">I have been admitted</option>
              </select>
            </label>
            <div className="section-heading bordered">
              <div>
                <h2>Your next chapter</h2>
              </div>
            </div>
            <label>
              Preferred course
              <input
                name="preferred_course"
                maxLength={150}
                defaultValue={user.profile?.preferred_course || ""}
                placeholder="e.g. Computer Science"
              />
            </label>
            <div className="field-grid">
              <label>
                UTME score <span className="optional">optional</span>
                <input
                  name="jamb_score"
                  type="number"
                  min={0}
                  max={400}
                  defaultValue={user.profile?.jamb_score ?? ""}
                  placeholder="Out of 400"
                />
              </label>
              <label>
                Admission year <span className="optional">optional</span>
                <input
                  name="jamb_year"
                  type="number"
                  min={2000}
                  max={2100}
                  defaultValue={user.profile?.jamb_year ?? ""}
                  placeholder="e.g. 2026"
                />
              </label>
            </div>
            <label>
              Universities on your mind
              <input
                name="preferred_universities"
                defaultValue={
                  user.profile?.preferred_universities.join(", ") || ""
                }
                placeholder="Separate university names with a comma"
              />
            </label>
            {error && <ErrorNotice>{error}</ErrorNotice>}
            <div className="profile-save">
              <span className="save-status" role="status">
                {saved && (
                  <>
                    <Check size={16} />
                    Your preferences are saved
                  </>
                )}
              </span>
              <button className="button primary" disabled={pending}>
                {pending ? (
                  <Spinner label="Saving" />
                ) : (
                  <>
                    <Save size={16} />
                    Save preferences
                  </>
                )}
              </button>
            </div>
          </form>
          <aside>
            <div className="personal-note">
              <span className="eyebrow">A GUIDE THAT KNOWS YOUR GOALS</span>
              <Sparkles size={25} />
              <h2>
                Good guidance
                <br />
                starts with you.
              </h2>
              <p>
                Your course, score and preferences help make each conversation
                more relevant.
              </p>
              <p>You can update these details whenever your plans change.</p>
            </div>
            <div className="panel privacy-panel">
              <ShieldCheck size={21} />
              <h3>You’re in control</h3>
              <p>
                Your profile and conversations are stored with your account.
                Chat context may be shared with the configured AI provider to
                answer your questions. It may search the public web for current
                information.
              </p>
              <p>
                Deleting your account removes your stored profile, conversations
                and feedback.
              </p>
              <button
                className="text-button danger-text"
                type="button"
                onClick={() => {
                  setDeleteOpen(true);
                  setDeleteError("");
                  setConfirmation("");
                }}
              >
                <Trash2 size={14} />
                Delete my account
              </button>
            </div>
          </aside>
        </div>
      )}
      {deleteOpen && (
        <Modal
          title="Permanently delete your account?"
          onClose={() => {
            if (!deleting) setDeleteOpen(false);
          }}
        >
          <form className="modal-content" onSubmit={deleteAccount}>
            <p>
              This will permanently remove your profile, conversations, messages
              and feedback. You will be signed out.
            </p>
            <label>
              Type DELETE to confirm
              <input
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                autoComplete="off"
                required
                pattern="DELETE"
              />
            </label>
            {deleteError && <ErrorNotice>{deleteError}</ErrorNotice>}
            <div className="modal-actions">
              <button
                className="button secondary"
                type="button"
                disabled={deleting}
                onClick={() => setDeleteOpen(false)}
              >
                Keep my account
              </button>
              <button
                className="button danger"
                disabled={confirmation !== "DELETE" || deleting}
              >
                {deleting ? (
                  <Spinner label="Deleting" />
                ) : (
                  <>
                    Delete account
                    <ArrowRight size={15} />
                  </>
                )}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
