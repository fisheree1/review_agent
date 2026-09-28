import { apiFetch } from "./client";
import { parseResponse } from "./documents";
import type { Citation } from "./rag";

export interface ScopeDocument {
  document_id: string;
  version_id: number;
  filename: string;
}

export interface Collection {
  id: string;
  name: string;
  description: string;
  document_ids: string[];
}

export interface ScopeChoice {
  document_ids: string[];
  collection_ids: string[];
}

export interface Conversation {
  group_id?: string | null;
  updated_at?: string;
  id: string;
  title: string;
  scope: ScopeDocument[];
  created_at: string;
}

export interface ConversationMessage {
  run_id?: string | null;
  id: string;
  question: string;
  status: "queued" | "processing" | "answered" | "insufficient" | "failed" | "cancelled";
  scope: ScopeDocument[];
  answer: { insufficient_evidence: boolean; claims: { text: string; citations: Citation[] }[] } | null;
  task_result?: {
    kind: "quiz" | "review" | "clarification";
    text: string;
    quiz_id: string | null;
    attempt_id: string | null;
    title: string | null;
  } | null;
  feedback: "helpful" | "unhelpful" | "citation_inaccurate" | null;
  failure_message: string | null;
}

export interface ConversationDetail extends Conversation {
  messages: ConversationMessage[];
}

export type QuestionKind = "single" | "multiple" | "true_false" | "short";

export interface QuizConfig {
  generation_mode?: "standard" | "agent";
  type_counts: Record<QuestionKind, number>;
  difficulty: "easy" | "medium" | "hard";
  language: "zh" | "en";
  topic: string;
}

export interface Quiz {
  id: string;
  title: string;
  status: "queued" | "processing" | "ready" | "failed";
  config: QuizConfig;
  scope: ScopeDocument[];
  question_count: number;
  failure_message: string | null;
}

export interface QuizQuestion {
  id: string;
  ordinal: number;
  kind: QuestionKind;
  difficulty: string;
  topic: string;
  stem: string;
  options: string[];
  answer: string | string[] | boolean | null;
  explanation: string | null;
  sources: Citation[];
}

export interface QuizDetail extends Quiz {
  questions: QuizQuestion[];
}

export interface AttemptSummary {
  id: string;
  status: "in_progress" | "grading" | "submitted" | "failed";
  score: number | null;
  weak_topics: string[] | null;
}

export interface AttemptQuestion extends QuizQuestion {
  response: string | string[] | boolean | null;
  earned: number | null;
  feedback: string | null;
  grading_method: string | null;
}

export interface AttemptDetail extends AttemptSummary {
  revision: number;
  failure_code: string | null;
  questions: AttemptQuestion[];
}

async function request<T>(path: string, method = "GET", body?: unknown, key?: string): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (key) headers["Idempotency-Key"] = key;
  return parseResponse<T>(await apiFetch(`/api/v1${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  }));
}

export const listCollections = () => request<Collection[]>("/collections");
export const createCollection = (name: string, description: string, document_ids: string[] = []) => request<Collection>("/collections", "POST", { name, description, document_ids });
export const setCollectionDocuments = (id: string, document_ids: string[]) => request<Collection>(`/collections/${id}/documents`, "PUT", { document_ids });
export async function deleteCollection(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/collections/${id}`, { method: "DELETE" });
  if (!response.ok) await parseResponse(response);
}

export const listConversations = (before?: string) => request<Conversation[]>(`/conversations${before ? `?before=${encodeURIComponent(before)}` : ""}`);
export const createConversation = (title: string, scope: ScopeChoice, key?: string) => request<Conversation>("/conversations", "POST", { title, ...scope }, key);
export const getConversation = (id: string) => request<ConversationDetail>(`/conversations/${id}`);
export const setConversationScope = (id: string, scope: ScopeChoice) => request<Conversation>(`/conversations/${id}/scope`, "PUT", scope);
export const askConversation = (id: string, question: string, key: string) => request<ConversationMessage>(`/conversations/${id}/messages`, "POST", { question }, key);
export const cancelConversationMessage = (id: string, messageId: string) => request<ConversationMessage>(`/conversations/${id}/messages/${messageId}:cancel`, "POST");
export const rateConversationMessage = (id: string, messageId: string, rating: NonNullable<ConversationMessage["feedback"]>, key: string) => request<{ rating: string }>(`/conversations/${id}/messages/${messageId}/feedback`, "PUT", { rating }, key);

export const listQuizzes = () => request<Quiz[]>("/quizzes");
export const createQuiz = (title: string, config: QuizConfig, scope: ScopeChoice, key: string) => request<Quiz>("/quizzes", "POST", { title, config, ...scope }, key);
export const getQuiz = (id: string) => request<QuizDetail>(`/quizzes/${id}`);
export const listAttempts = (id: string) => request<AttemptSummary[]>(`/quizzes/${id}/attempts`);
export const startAttempt = (id: string, key: string) => request<AttemptSummary>(`/quizzes/${id}/attempts`, "POST", undefined, key);
export const getAttempt = (quizId: string, attemptId: string) => request<AttemptDetail>(`/quizzes/${quizId}/attempts/${attemptId}`);
export const saveQuizAnswer = (quizId: string, attemptId: string, questionId: string, response: string | string[] | boolean, expected_revision?: number) => request<{ question_id: string; revision: number }>(`/quizzes/${quizId}/attempts/${attemptId}/answers/${questionId}`, "PUT", { response, expected_revision });
export const submitAttempt = (quizId: string, attemptId: string) => request<AttemptSummary>(`/quizzes/${quizId}/attempts/${attemptId}:submit`, "POST");
export const retryGrading = (quizId: string, attemptId: string) => request<AttemptSummary>(`/quizzes/${quizId}/attempts/${attemptId}:retry-grading`, "POST");

export interface AgentRun {
  id: string;
  status: "queued" | "running" | "waiting_input" | "waiting_result" | "blocked" | "completed" | "failed" | "cancelled" | "expired";
  stage: string;
  revision: number;
  scope: ScopeDocument[];
  plan: { action: string; summary_request?: string; review_after_submit?: boolean; practice_after_review?: boolean } | null;
  outputs: { kind: "summary" | "quiz" | "review" | "notice"; quiz_id?: string; attempt_id?: string; text: string; title?: string }[];
  clarification: string | null;
  failure_message: string | null;
  expires_at: string;
}
export const getAgentRun = (id: string) => request<AgentRun>(`/agent-runs/${id}`);
export const cancelAgentRun = (id: string) => request<AgentRun>(`/agent-runs/${id}:cancel`, "POST");
export const respondAgentRun = (id: string, answer: string, expected_revision: number, key: string) => request<AgentRun>(`/agent-runs/${id}:respond`, "POST", { answer, expected_revision }, key);

export interface LearningResume {
  conversation: { id: string; title: string; working: boolean } | null;
  attempt: { id: string; quiz_id: string; title: string; status: "in_progress" | "grading" } | null;
}
export const getLearningResume = () => request<LearningResume>("/learning/resume");

export interface ConversationGroup { id: string; name: string }
export const listConversationGroups = () => request<ConversationGroup[]>("/conversation-groups");
export const createConversationGroup = (name: string) => request<ConversationGroup>("/conversation-groups", "POST", { name });
export const renameConversationGroup = (id: string, name: string) => request<ConversationGroup>(`/conversation-groups/${id}`, "PATCH", { name });
export async function deleteConversationGroup(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/conversation-groups/${id}`, { method: "DELETE" });
  if (!response.ok) await parseResponse(response);
}
export const updateConversation = (id: string, changes: { title?: string; group_id?: string | null }) => request<Conversation>(`/conversations/${id}`, "PATCH", changes);
export async function deleteConversation(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/conversations/${id}`, { method: "DELETE" });
  if (!response.ok) await parseResponse(response);
}
export interface StudyCalendar {
  month: string; today: string; timezone: string; dates: string[];
  checked_today: boolean; streak: number; total: number;
}
export const getStudyCalendar = (month: string, timezone: string) => request<StudyCalendar>(`/learning/checkins?${new URLSearchParams({ month, timezone })}`);
export const checkinToday = (timezone: string) => request<StudyCalendar>("/learning/checkins", "POST", { timezone });
