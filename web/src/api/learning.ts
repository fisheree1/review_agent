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
  id: string;
  title: string;
  scope: ScopeDocument[];
  created_at: string;
}

export interface ConversationMessage {
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
export const createCollection = (name: string, description: string) => request<Collection>("/collections", "POST", { name, description });
export const setCollectionDocuments = (id: string, document_ids: string[]) => request<Collection>(`/collections/${id}/documents`, "PUT", { document_ids });
export async function deleteCollection(id: string): Promise<void> {
  const response = await apiFetch(`/api/v1/collections/${id}`, { method: "DELETE" });
  if (!response.ok) await parseResponse(response);
}

export const listConversations = () => request<Conversation[]>("/conversations");
export const createConversation = (title: string, scope: ScopeChoice) => request<Conversation>("/conversations", "POST", { title, ...scope });
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
export const saveQuizAnswer = (quizId: string, attemptId: string, questionId: string, response: string | string[] | boolean) => request<{ question_id: string }>(`/quizzes/${quizId}/attempts/${attemptId}/answers/${questionId}`, "PUT", { response });
export const submitAttempt = (quizId: string, attemptId: string) => request<AttemptSummary>(`/quizzes/${quizId}/attempts/${attemptId}:submit`, "POST");
export const retryGrading = (quizId: string, attemptId: string) => request<AttemptSummary>(`/quizzes/${quizId}/attempts/${attemptId}:retry-grading`, "POST");
