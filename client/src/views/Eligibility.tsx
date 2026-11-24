import { useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowRight,
  CheckCircle2,
  CircleHelp,
  ClipboardCheck,
  GraduationCap,
  ListChecks,
  Plus,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Eligibility } from "../lib/types";
import {
  EmptyState,
  ErrorNotice,
  PageHeading,
  SourceLink,
  Spinner,
} from "../components/ui";

const subjects = [
  "English",
  "Mathematics",
  "Biology",
  "Chemistry",
  "Physics",
  "Economics",
  "Government",
  "Literature in English",
  "Geography",
  "Agricultural Science",
  "Commerce",
  "Financial Accounting",
  "Christian Religious Studies",
  "Islamic Religious Studies",
  "History",
  "French",
  "Fine Arts",
  "Computer Studies",
  "Civic Education",
];
const grades = ["A1", "B2", "B3", "C4", "C5", "C6", "D7", "E8", "F9"];
const statuses = {
  meets_requirements: "You meet the published requirements",
  does_not_meet_requirements: "Some requirements are not met",
  insufficient_data: "A little more information is needed",
};
interface GradeRow {
  id: number;
  subject: string;
  grade: string;
}
export function EligibilityView({ onSignIn }: { onSignIn: () => void }) {
  const { user } = useAuth();
  const [jambSubjects, setJambSubjects] = useState(["English", "", "", ""]);
  const [rows, setRows] = useState<GradeRow[]>(() => {
    const saved = Object.entries(user?.profile?.olevel_results || {});
    return (
      saved.length
        ? saved
        : [
            ["English", ""],
            ["Mathematics", ""],
            ["", ""],
            ["", ""],
            ["", ""],
          ]
    ).map(([subject, grade], id) => ({
      id,
      subject:
        subjects.find(
          (option) => option.toLowerCase() === subject.toLowerCase(),
        ) || subject,
      grade,
    }));
  });
  const [result, setResult] = useState<Eligibility | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [courseName, setCourseName] = useState(
    user?.profile?.preferred_course || "",
  );
  function updateRow(id: number, field: "subject" | "grade", value: string) {
    setRows((old) =>
      old.map((row) => (row.id === id ? { ...row, [field]: value } : row)),
    );
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user?.data_consent_at) {
      onSignIn();
      return;
    }
    setPending(true);
    setError("");
    setResult(null);
    const form = new FormData(event.currentTarget);
    const selected = rows.filter((row) => row.subject && row.grade);
    const unique = new Set(selected.map((row) => row.subject.toLowerCase()));
    if (
      unique.size !== selected.length ||
      new Set(jambSubjects.filter(Boolean)).size !==
        jambSubjects.filter(Boolean).length
    ) {
      setError("Please select each subject only once.");
      setPending(false);
      return;
    }
    if (rows.some((row) => Boolean(row.grade) !== Boolean(row.subject))) {
      setError(
        "Choose both a subject and a grade for each result, or leave the whole row blank.",
      );
      setPending(false);
      return;
    }
    try {
      setResult(
        await api.post<Eligibility>("/admissions/eligibility", {
          course_name: courseName.trim(),
          jamb_score: Number(form.get("jamb_score")),
          jamb_subjects: jambSubjects.filter(Boolean),
          olevel_results: Object.fromEntries(
            selected.map((row) => [row.subject, row.grade]),
          ),
          ...(form.get("year") ? { year: Number(form.get("year")) } : {}),
        }),
      );
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      setPending(false);
    }
  }
  return (
    <div className="page-container">
      <PageHeading
        eyebrow="KNOW WHERE YOU STAND"
        title="Turn your results into a next step."
      >
        Check your UTME score and subjects against published course
        requirements.
      </PageHeading>
      <div className="eligibility-layout">
        <form className="panel eligibility-form" onSubmit={submit}>
          <div className="section-heading">
            <span className="section-icon">
              <GraduationCap size={19} />
            </span>
            <div>
              <h2>Your study plans</h2>
              <p>Start with the course you have in mind.</p>
            </div>
            <span className="step-tag">01 / 03</span>
          </div>
          <label>
            Preferred course
            <input
              value={courseName}
              onChange={(e) => setCourseName(e.target.value)}
              required
              minLength={2}
              maxLength={150}
              placeholder="e.g. Computer Science"
            />
          </label>
          <div className="field-grid">
            <label>
              UTME score
              <input
                name="jamb_score"
                type="number"
                min={0}
                max={400}
                required
                defaultValue={user?.profile?.jamb_score ?? ""}
                placeholder="Your score out of 400"
              />
            </label>
            <label>
              Admission year <span className="optional">optional</span>
              <input
                name="year"
                type="number"
                min={2000}
                max={2100}
                defaultValue={user?.profile?.jamb_year ?? ""}
                placeholder="Current published cycle"
              />
            </label>
          </div>
          <div className="section-heading bordered">
            <span className="section-icon">
              <ListChecks size={19} />
            </span>
            <div>
              <h2>Your UTME subjects</h2>
              <p>Select the subjects you sat for.</p>
            </div>
            <span className="step-tag">02 / 03</span>
          </div>
          <div className="field-grid">
            {jambSubjects.map((value, index) => (
              <label key={index}>
                Subject {index + 1}
                <select
                  aria-label={`UTME subject ${index + 1}`}
                  value={value}
                  onChange={(e) =>
                    setJambSubjects((old) =>
                      old.map((subject, i) =>
                        i === index ? e.target.value : subject,
                      ),
                    )
                  }
                >
                  <option value="">Select a subject</option>
                  {subjects.map((subject) => (
                    <option key={subject}>{subject}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <div className="section-heading bordered">
            <span className="section-icon">
              <ClipboardCheck size={19} />
            </span>
            <div>
              <h2>Your O’Level results</h2>
              <p>Add your WAEC or NECO subject grades.</p>
            </div>
            <span className="step-tag">03 / 03</span>
          </div>
          <div className="grades-header">
            <span>SUBJECT</span>
            <span>GRADE</span>
          </div>
          <div className="grade-rows">
            {rows.map((row, index) => (
              <div className="grade-row" key={row.id}>
                <select
                  aria-label={`O’Level subject ${index + 1}`}
                  value={row.subject}
                  onChange={(e) => updateRow(row.id, "subject", e.target.value)}
                >
                  <option value="">Select a subject</option>
                  {!subjects.includes(row.subject) && row.subject && (
                    <option>{row.subject}</option>
                  )}
                  {subjects.map((subject) => (
                    <option key={subject}>{subject}</option>
                  ))}
                </select>
                <select
                  aria-label={`O’Level grade ${index + 1}`}
                  value={row.grade}
                  onChange={(e) => updateRow(row.id, "grade", e.target.value)}
                >
                  <option value="">Grade</option>
                  {grades.map((grade) => (
                    <option key={grade}>{grade}</option>
                  ))}
                </select>
                <button
                  type="button"
                  className="icon-button"
                  aria-label={`Remove O’Level result ${index + 1}`}
                  onClick={() =>
                    setRows((old) => old.filter((item) => item.id !== row.id))
                  }
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
          </div>
          {rows.length < 9 && (
            <button
              className="text-button add-grade"
              type="button"
              onClick={() =>
                setRows((old) => [
                  ...old,
                  { id: Date.now(), subject: "", grade: "" },
                ])
              }
            >
              <Plus size={15} />
              Add another subject
            </button>
          )}
          {error && <ErrorNotice>{error}</ErrorNotice>}
          <button
            className="button primary full check-button"
            disabled={pending}
          >
            {pending ? (
              <Spinner label="Checking requirements" />
            ) : (
              <>
                Check my eligibility
                <ArrowRight size={17} />
              </>
            )}
          </button>
          <p className="form-footnote">
            <ShieldCheck size={14} />
            Your results are checked against each institution’s published
            requirements.
          </p>
        </form>
        <aside className="eligibility-results" aria-live="polite">
          {result ? (
            <div className={`panel result-panel result-${result.status}`}>
              <span className="result-symbol">
                {result.is_eligible ? (
                  <CheckCircle2 size={28} />
                ) : (
                  <CircleHelp size={28} />
                )}
              </span>
              <span className="eyebrow">YOUR ELIGIBILITY CHECK</span>
              <h2>{statuses[result.status]}</h2>
              <p>
                {result.reason ||
                  "Review each institution’s requirements and confirm details with its admissions office."}
              </p>
              {result.assessments.map((assessment, index) => (
                <div
                  className="assessment"
                  key={`${assessment.university}-${index}`}
                >
                  <div className="assessment-heading">
                    <strong>{assessment.university}</strong>
                    <span
                      className={`badge ${assessment.status === "meets_requirements" ? "positive" : ""}`}
                    >
                      {assessment.status === "meets_requirements"
                        ? "Meets requirements"
                        : assessment.status === "insufficient_data"
                          ? "More data needed"
                          : "Requirements not met"}
                    </span>
                  </div>
                  <small>
                    {assessment.course} · {assessment.year} · Published cut-off:{" "}
                    {assessment.cutoff}
                  </small>
                  {assessment.missing_subjects.length > 0 && (
                    <p>
                      Missing UTME subjects:{" "}
                      {assessment.missing_subjects.join(", ")}
                    </p>
                  )}
                  {assessment.failed_olevel.length > 0 && (
                    <ul>
                      {assessment.failed_olevel.map((item) => (
                        <li key={item}>{item}</li>
                      ))}
                    </ul>
                  )}
                  <SourceLink url={assessment.source_url}>
                    View publication
                  </SourceLink>
                </div>
              ))}
              {result.assessments.length === 0 && (
                <div className="notice neutral">
                  No matching course requirements are published in the current
                  knowledge base. Try the institution’s full course name and the
                  relevant admission year.
                </div>
              )}
            </div>
          ) : (
            <>
              <div className="panel result-placeholder">
                <EmptyState
                  icon={<ClipboardCheck size={27} />}
                  title="A clearer picture starts here"
                >
                  Your results will appear here. We’ll show what you meet,
                  what’s missing, and where more information is needed.
                </EmptyState>
                <div className="result-placeholder-line" />
                <div className="result-placeholder-line short" />
              </div>
              <div className="info-card">
                <CircleHelp size={18} />
                <div>
                  <h3>Guidance you can act on</h3>
                  <p>
                    Meeting entry requirements is one step. Screening, available
                    places and institution-specific conditions also influence
                    admission.
                  </p>
                </div>
              </div>
            </>
          )}
        </aside>
      </div>
    </div>
  );
}
