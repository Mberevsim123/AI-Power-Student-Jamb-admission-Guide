import { useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowRight,
  CheckCircle2,
  Circle,
  CircleAlert,
  CircleHelp,
  ClipboardCheck,
  GraduationCap,
  ListChecks,
  Plus,
  Trash2,
  XCircle,
} from "lucide-react";
import { api, errorMessage } from "../lib/api";
import { useAuth } from "../lib/auth";
import type { Eligibility, GeneralCheck } from "../lib/types";
import { ErrorNotice, PageHeading, SourceLink, Spinner } from "../components/ui";

// Use of English is compulsory, so it is fixed as the first UTME subject.
const utmeSubjects = [
  "Mathematics",
  "Physics",
  "Chemistry",
  "Biology",
  "Agricultural Science",
  "Economics",
  "Government",
  "Literature in English",
  "Geography",
  "Commerce",
  "Principles of Accounts",
  "Christian Religious Studies",
  "Islamic Religious Studies",
  "History",
  "Further Mathematics",
  "Computer Studies",
  "Fine Arts",
  "Music",
  "Home Economics",
  "Physical and Health Education",
  "Technical Drawing",
  "Yoruba",
  "Igbo",
  "Hausa",
  "French",
  "Arabic",
];
const subjects = [
  "English Language",
  "Mathematics",
  "Physics",
  "Chemistry",
  "Biology",
  "Agricultural Science",
  "Economics",
  "Government",
  "Literature in English",
  "Geography",
  "Commerce",
  "Financial Accounting",
  "Christian Religious Studies",
  "Islamic Religious Studies",
  "History",
  "Civic Education",
  "Further Mathematics",
  "Computer Studies",
  "Data Processing",
  "Technical Drawing",
  "Fine Arts",
  "Music",
  "Food and Nutrition",
  "Animal Husbandry",
  "Marketing",
  "Yoruba",
  "Igbo",
  "Hausa",
  "French",
  "Arabic",
];
const grades = ["A1", "B2", "B3", "C4", "C5", "C6", "D7", "E8", "F9"];
const statuses = {
  meets_requirements: "You meet the published requirements",
  does_not_meet_requirements: "Some requirements are not met",
  insufficient_data: "Not enough published information to decide",
};
const checkIcons = {
  pass: <CheckCircle2 size={17} />,
  fail: <XCircle size={17} />,
  warn: <CircleAlert size={17} />,
  missing: <Circle size={17} />,
};
function Check({ check }: { check: GeneralCheck }) {
  return (
    <li className={`check check-${check.status}`}>
      <span className="check-icon" aria-label={check.status}>
        {checkIcons[check.status]}
      </span>
      <span>
        <strong>{check.label}</strong>
        <small>{check.detail}</small>
      </span>
    </li>
  );
}
interface GradeRow {
  id: number;
  subject: string;
  grade: string;
}
export function EligibilityView({ onSignIn }: { onSignIn: () => void }) {
  const { user } = useAuth();
  const [jambSubjects, setJambSubjects] = useState(["", "", ""]);
  const [rows, setRows] = useState<GradeRow[]>(() => {
    const saved = Object.entries(user?.profile?.olevel_results || {});
    return (
      saved.length
        ? saved
        : [
            ["English Language", ""],
            ["Mathematics", ""],
            ["", ""],
            ["", ""],
            ["", ""],
          ]
    ).map(([subject, grade], id) => ({
      id,
      subject:
        subjects.find(
          (option) =>
            option.toLowerCase() === subject.toLowerCase() ||
            option.toLowerCase() === `${subject.toLowerCase()} language`,
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
          jamb_subjects: ["Use of English", ...jambSubjects.filter(Boolean)],
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
        title="Eligibility check"
      >
        Check your UTME score, subjects and O’Level results against JAMB’s
        national rules and each institution’s published requirements.
      </PageHeading>
      <div className="eligibility-layout">
        <form className="panel eligibility-form" onSubmit={submit}>
          <div className="section-heading">
            <span className="section-icon">
              <GraduationCap size={19} />
            </span>
            <div>
              <h2>Course and score</h2>
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
              <h2>UTME subjects</h2>
              <p>Use of English plus your three other subjects.</p>
            </div>
            <span className="step-tag">02 / 03</span>
          </div>
          <div className="field-grid">
            <label>
              Subject 1
              <input value="Use of English" readOnly aria-readonly="true" />
            </label>
            {jambSubjects.map((value, index) => (
              <label key={index}>
                Subject {index + 2}
                <select
                  aria-label={`UTME subject ${index + 2}`}
                  value={value}
                  required
                  onChange={(e) =>
                    setJambSubjects((old) =>
                      old.map((subject, i) =>
                        i === index ? e.target.value : subject,
                      ),
                    )
                  }
                >
                  <option value="">Select a subject</option>
                  {utmeSubjects.map((subject) => (
                    <option
                      key={subject}
                      disabled={
                        jambSubjects.includes(subject) && value !== subject
                      }
                    >
                      {subject}
                    </option>
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
              <h2>O’Level results</h2>
              <p>WAEC, NECO or NABTEB grades.</p>
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
            Checked against JAMB’s national rules and each institution’s
            published requirements for the admission year.
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
              <h2>{statuses[result.status]}</h2>
              {result.reason && <p>{result.reason}</p>}

              <h3 className="result-section">National entry rules</h3>
              <ul className="checks">
                {result.general_checks.map((check) => (
                  <Check key={check.id} check={check} />
                ))}
              </ul>

              {result.assessments.length > 0 && (
                <>
                  <h3 className="result-section">
                    Institutions ({result.assessments.length})
                  </h3>
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
                              : "Not met"}
                        </span>
                      </div>
                      <small>
                        {assessment.course} · {assessment.year} · Cut-off{" "}
                        {assessment.cutoff} · Your margin{" "}
                        {assessment.margin >= 0 ? "+" : ""}
                        {assessment.margin}
                      </small>
                      {assessment.missing_subjects.length > 0 && (
                        <p>
                          Required UTME subjects you didn’t take:{" "}
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
                        Published requirements
                      </SourceLink>
                    </div>
                  ))}
                </>
              )}

              {result.next_steps.length > 0 && (
                <>
                  <h3 className="result-section">Next steps</h3>
                  <ol className="next-steps">
                    {result.next_steps.map((step) => (
                      <li key={step}>{step}</li>
                    ))}
                  </ol>
                </>
              )}
              <div className="result-links">
                {result.official_links.map((link) => (
                  <SourceLink key={link.url} url={link.url}>
                    {link.label}
                  </SourceLink>
                ))}
              </div>
            </div>
          ) : (
            <div className="panel result-placeholder">
              <p className="muted">Your result will appear here.</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
