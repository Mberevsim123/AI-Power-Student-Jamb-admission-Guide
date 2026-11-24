export interface Tokens {
  access_token: string;
  refresh_token: string;
}
export interface Profile {
  display_name: string | null;
  jamb_score: number | null;
  jamb_year: number | null;
  academic_stage: string;
  preferred_course: string | null;
  preferred_universities: string[];
  state_of_origin: string | null;
  olevel_results: Record<string, string>;
}
export interface User {
  id: number;
  email: string;
  username: string;
  data_consent_at: string | null;
  profile: Profile | null;
}
export interface Source {
  doc_title: string;
  source_url?: string | null;
  cycle_year?: number | null;
  snippet?: string;
  origin?: "web" | "knowledge_base";
  retrieved_at?: string;
}
export interface Message {
  id: number;
  conversation_id: number;
  role: string;
  state: string;
  content: string | null;
  rag_sources: Source[];
  structured_context: Record<string, unknown>;
  created_at: string;
  error?: string;
  error_message?: string | null;
  /** Client-only: stable React key across the live → saved swap, and reveal flag. */
  client_key?: string;
  reveal?: boolean;
}
export interface Conversation {
  id: number;
  title: string;
  message_count: number;
  updated_at: string;
}
export interface ConversationDetail extends Conversation {
  messages: Message[];
}
export interface University {
  id: number;
  name: string;
  abbreviation: string | null;
  state: string;
  university_type: string;
  website_url: string | null;
}
export interface Course {
  id: number;
  name: string;
  faculty: string;
  jamb_subjects: string[];
  olevel_requirements: Record<string, string>;
  source_url: string | null;
  requirements_year: number | null;
  course_notes: string | null;
}
export interface Cutoff {
  university: string;
  course: string;
  score: number;
  year: number;
  is_official: boolean;
  source_url: string | null;
}
export interface Schedule {
  id: number;
  university: string;
  year: number;
  start_date: string;
  end_date: string;
  fee_ngn: string;
  eligibility: string;
  source_url: string;
}
export interface Assessment {
  university: string;
  course: string;
  cutoff: number;
  year: number;
  margin: number;
  source_url: string;
  status: string;
  missing_subjects: string[];
  failed_olevel: string[];
}
export interface Eligibility {
  status:
    "meets_requirements" | "does_not_meet_requirements" | "insufficient_data";
  is_eligible: boolean;
  reason: string | null;
  assessments: Assessment[];
}
export type View = "chat" | "universities" | "eligibility" | "profile";
export interface StreamMetadata {
  conversation_id: number;
  message_id: number;
}
export interface Nudge {
  kind: string;
  severity: "urgent" | "soon" | "info";
  title: string;
  detail: string;
  source_url?: string | null;
  prompt?: string | null;
}
export interface AgentOption {
  id: string;
  kind: "same_course_elsewhere" | "related_course" | "keep_target";
  label: string;
  university?: string | null;
  course?: string | null;
  cutoff?: number | null;
  margin?: number | null;
  year?: number | null;
  source_url?: string | null;
}
export interface JourneyStep {
  id: string;
  title: string;
  stage: string;
  hint: string;
  guide_url: string;
  prompt: string;
  done: boolean;
  by: string | null;
}
export interface Journey {
  stage: string;
  steps: JourneyStep[];
  completed: number;
  total: number;
  next: JourneyStep | null;
}
export interface KnownFact {
  key: string;
  label: string;
  value: string;
  source: string;
}
/** What the agent perceived, did and proposes for one turn. */
export interface AgentState {
  intent: string;
  affect: string;
  actions: { tool: string; summary: string }[];
  known: KnownFact[];
  nudges: Nudge[];
  options: AgentOption[];
  suggestions: string[];
  links?: OfficialLink[];
  profile_patch: Record<string, unknown>;
  ask: string | null;
  journey: {
    updated: string[];
    next: JourneyStep | null;
    completed: number;
    total: number;
  } | null;
  verification?: {
    checked: number;
    unsupported: { kind: string; value: string }[];
  };
}
export interface Briefing {
  greeting: string;
  recall: { course: string; universities: string[] } | null;
  known: KnownFact[];
  nudges: Nudge[];
  journey: Journey;
  profile: {
    completeness: number;
    missing: { field: string; label: string; starter: string }[];
  };
  suggestions: string[];
}
export interface OfficialLink {
  label: string;
  url: string;
  purpose: string;
  kind: "platform" | "institution";
}
