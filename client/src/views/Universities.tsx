import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import {
  ArrowRight,
  BookOpen,
  Building2,
  CalendarDays,
  GraduationCap,
  MapPin,
  Search,
} from "lucide-react";
import { api, ApiError, errorMessage } from "../lib/api";
import type { Course, Cutoff, Schedule, University } from "../lib/types";
import {
  EmptyState,
  ErrorNotice,
  Modal,
  PageHeading,
  SourceLink,
  Spinner,
} from "../components/ui";
import { displayName } from "../lib/format";

const date = (value: string) =>
  new Date(value).toLocaleDateString("en-NG", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
export function UniversityView({ onAsk }: { onAsk: (prompt: string) => void }) {
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(0);
  const [universities, setUniversities] = useState<University[]>([]);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<University | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    setPending(true);
    setError("");
    const params = new URLSearchParams({
      limit: "12",
      offset: String(page * 12),
    });
    if (search) params.set("name", search);
    if (type) params.set("university_type", type);
    api
      .get<University[]>(
        `/admissions/universities?${params}`,
        controller.signal,
      )
      .then(setUniversities)
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setPending(false);
      });
    return () => controller.abort();
  }, [search, type, page, retry]);
  function submit(event: FormEvent) {
    event.preventDefault();
    setPage(0);
    setSearch(query.trim());
    setRetry((old) => old + 1);
  }
  return (
    <div className="page-container">
      <PageHeading
        eyebrow="FIND YOUR FIT"
        title="There’s a place for your ambition."
      >
        Explore Nigerian universities and the published information behind your
        choices.
      </PageHeading>
      <form className="explorer-toolbar" onSubmit={submit}>
        <div className="search-field">
          <Search size={18} />
          <label className="sr-only" htmlFor="university-search">
            Search universities
          </label>
          <input
            id="university-search"
            placeholder="Name or abbreviation, e.g. UNILAG"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button className="button primary small" type="submit">
            Search
          </button>
        </div>
        <label className="type-filter">
          <span className="sr-only">University type</span>
          <select
            value={type}
            onChange={(e) => {
              setType(e.target.value);
              setPage(0);
            }}
          >
            <option value="">All institution types</option>
            <option value="federal">Federal universities</option>
            <option value="state">State universities</option>
            <option value="private">Private universities</option>
          </select>
        </label>
      </form>
      <div className="results-heading">
        <span>
          {pending
            ? "Finding your options…"
            : search
              ? `Results for “${search}”`
              : "Explore your options"}
        </span>
        <small>Published institution records</small>
      </div>
      {error ? (
        <ErrorNotice onRetry={() => setRetry((old) => old + 1)}>
          {error}
        </ErrorNotice>
      ) : pending ? (
        <div className="university-grid" aria-label="Loading universities">
          {Array.from({ length: 6 }, (_, i) => (
            <div className="panel university-skeleton" key={i}>
              <span />
              <div />
              <div />
            </div>
          ))}
        </div>
      ) : universities.length === 0 ? (
        <div className="panel">
          <EmptyState
            icon={<Building2 size={29} />}
            title={
              search
                ? "No universities match that search"
                : "Your options will appear here"
            }
          >
            {search
              ? "Try the full institution name or a different abbreviation."
              : "University information is being prepared. You can still ask a question in the admissions guide."}
          </EmptyState>
          {page > 0 && (
            <div className="pagination">
              <button
                className="button secondary small"
                onClick={() => setPage((old) => old - 1)}
              >
                Previous
              </button>
              <span>Page {page + 1}</span>
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="university-grid">
            {universities.map((university) => (
              <button
                className="panel university-card"
                key={university.id}
                onClick={() => setSelected(university)}
              >
                <div className="university-card-top">
                  <span className="university-monogram">
                    {university.abbreviation?.slice(0, 3) ||
                      university.name
                        .split(" ")
                        .map((word) => word[0])
                        .slice(0, 3)
                        .join("")}
                  </span>
                  <span className="badge">{university.university_type}</span>
                </div>
                <h2>{displayName(university.name, university.state)}</h2>
                <p>
                  <MapPin size={14} />
                  {university.state} State
                </p>
                <div className="university-card-footer">
                  <span>Explore requirements</span>
                  <ArrowRight size={17} />
                </div>
              </button>
            ))}
          </div>
          <div className="pagination">
            <button
              className="button secondary small"
              disabled={page === 0}
              onClick={() => setPage((old) => old - 1)}
            >
              Previous
            </button>
            <span>Page {page + 1}</span>
            <button
              className="button secondary small"
              disabled={universities.length < 12}
              onClick={() => setPage((old) => old + 1)}
            >
              Next
            </button>
          </div>
        </>
      )}
      <div className="explorer-note">
        <ShieldIcon />
        <p>
          Admission requirements can change by programme and year. Open the
          source publication when making your shortlist.
        </p>
      </div>
      {selected && (
        <UniversityDetail
          university={selected}
          onClose={() => setSelected(null)}
          onAsk={() => {
            onAsk(`Tell me about admission requirements for ${selected.name}.`);
            setSelected(null);
          }}
        />
      )}
    </div>
  );
}
function ShieldIcon() {
  return <BookOpen size={17} />;
}
function UniversityDetail({
  university,
  onClose,
  onAsk,
}: {
  university: University;
  onClose: () => void;
  onAsk: () => void;
}) {
  const [tab, setTab] = useState<"courses" | "cutoffs" | "screening">(
    "courses",
  );
  const [data, setData] = useState<{
    courses: Course[];
    cutoffs: Cutoff[];
    screening: Schedule[];
  }>({ courses: [], cutoffs: [], screening: [] });
  const [pending, setPending] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    async function optional<T>(path: string): Promise<T[]> {
      try {
        return await api.get<T[]>(path, controller.signal);
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return [];
        throw error;
      }
    }
    setPending(true);
    setError("");
    Promise.all([
      optional<Course>(`/admissions/universities/${university.id}/courses`),
      optional<Cutoff>(
        `/admissions/cutoffs?university=${encodeURIComponent(university.name)}`,
      ),
      optional<Schedule>(
        `/admissions/post-utme?university=${encodeURIComponent(university.name)}`,
      ),
    ])
      .then(([courses, cutoffs, screening]) =>
        setData({ courses, cutoffs, screening }),
      )
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setPending(false);
      });
    return () => controller.abort();
  }, [university, retry]);
  return (
    <Modal title={university.name} wide onClose={onClose}>
      <div className="modal-content university-detail">
        <div className="detail-meta">
          <span>
            <MapPin size={14} />
            {university.state}
          </span>
          <span className="badge">{university.university_type}</span>
          <SourceLink url={university.website_url}>Official website</SourceLink>
        </div>
        <div
          className="detail-tabs"
          role="tablist"
          aria-label="University information"
        >
          {(["courses", "cutoffs", "screening"] as const).map((value) => (
            <button
              key={value}
              role="tab"
              aria-selected={tab === value}
              aria-controls={`university-${value}`}
              id={`tab-${value}`}
              onClick={() => setTab(value)}
            >
              {value === "courses"
                ? "Courses & subjects"
                : value === "cutoffs"
                  ? "Cut-off marks"
                  : "Post-UTME"}
            </button>
          ))}
        </div>
        <div
          role="tabpanel"
          id={`university-${tab}`}
          aria-labelledby={`tab-${tab}`}
        >
          {pending ? (
            <Spinner label="Loading published information" />
          ) : error ? (
            <ErrorNotice onRetry={() => setRetry((old) => old + 1)}>
              {error}
            </ErrorNotice>
          ) : data[tab].length === 0 ? (
            <EmptyState
              icon={
                tab === "screening" ? (
                  <CalendarDays size={24} />
                ) : (
                  <GraduationCap size={26} />
                )
              }
              title="No published information yet"
            >
              Check the institution’s official admissions office for the latest
              information.
            </EmptyState>
          ) : tab === "courses" ? (
            <div className="course-list">
              {data.courses.map((course) => (
                <div className="course-item" key={course.id}>
                  <div>
                    <h3>{course.name}</h3>
                    <small>
                      {course.faculty}
                      {course.requirements_year
                        ? ` · ${course.requirements_year}`
                        : " · Year not supplied"}
                    </small>
                  </div>
                  <p>
                    <strong>UTME subjects</strong>{" "}
                    {course.jamb_subjects.join(", ") || "Not published"}
                  </p>
                  <p>
                    <strong>O’Level grades</strong>{" "}
                    {Object.entries(course.olevel_requirements)
                      .map(([subject, grade]) => `${subject}: ${grade}`)
                      .join(" · ") || "Not published"}
                  </p>
                  {course.course_notes && (
                    <p className="muted">{course.course_notes}</p>
                  )}
                  <SourceLink url={course.source_url}>
                    Course requirements
                  </SourceLink>
                </div>
              ))}
            </div>
          ) : tab === "cutoffs" ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Course</th>
                    <th>Score</th>
                    <th>Year</th>
                    <th>Publication</th>
                  </tr>
                </thead>
                <tbody>
                  {data.cutoffs.map((cutoff, index) => (
                    <tr key={index}>
                      <td>{cutoff.course}</td>
                      <td>
                        <strong>{cutoff.score}</strong>
                      </td>
                      <td>{cutoff.year}</td>
                      <td>
                        <SourceLink url={cutoff.source_url}>
                          {cutoff.is_official
                            ? "Official source"
                            : "Estimate — confirm"}
                        </SourceLink>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div>
              {data.screening.map((schedule) => (
                <div key={schedule.id} className="course-item">
                  <h3>{schedule.year} screening</h3>
                  <p>
                    {date(schedule.start_date)} – {date(schedule.end_date)}
                  </p>
                  <p>
                    Published fee:{" "}
                    {new Intl.NumberFormat("en-NG", {
                      style: "currency",
                      currency: "NGN",
                    }).format(Number(schedule.fee_ngn))}
                  </p>
                  <p>{schedule.eligibility}</p>
                  <SourceLink url={schedule.source_url}>
                    Screening announcement
                  </SourceLink>
                </div>
              ))}
            </div>
          )}
        </div>
        <button className="button primary full detail-ask" onClick={onAsk}>
          Ask JAMB Guide about this university
          <ArrowRight size={16} />
        </button>
      </div>
    </Modal>
  );
}
