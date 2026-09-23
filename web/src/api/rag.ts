import { apiFetch } from "./client";
import { parseResponse, type CitationLocator } from "./documents";

export interface IndexStatus {
  status: "not_indexed" | "queued" | "processing" | "ready" | "failed";
  completed: number;
  total: number;
  failure_message: string | null;
}

export interface Citation {
  source_id: string;
  quote: string;
  unit: number;
  locator: CitationLocator;
  document_id?: string | null;
  version_id?: number | null;
}

export interface Question {
  id: string;
  question: string;
  version: number;
  status: "queued" | "processing" | "answered" | "insufficient" | "failed" | "cancelled";
  answer: { insufficient_evidence: boolean; claims: { text: string; citations: Citation[] }[] } | null;
  failure_message: string | null;
}

const path = (id: string) => `/api/v1/documents/${id}`;
export const getIndex = async (id: string) => parseResponse<IndexStatus>(await apiFetch(`${path(id)}/index`));
export const getQuestions = async (id: string) => parseResponse<Question[]>(await apiFetch(`${path(id)}/questions`));
export const startIndex = async (id: string, key: string) => parseResponse<IndexStatus>(await apiFetch(`${path(id)}/index`, {
  method: "POST", headers: { "Idempotency-Key": key },
}));
export const askQuestion = async (id: string, question: string, key: string) => parseResponse<Question>(await apiFetch(`${path(id)}/questions`, {
  method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key }, body: JSON.stringify({ question }),
}));
export const cancelQuestion = async (id: string, questionId: string) => parseResponse<Question>(await apiFetch(`${path(id)}/questions/${questionId}:cancel`, { method: "POST" }));
export const isAnswering = (question: Question) => question.status === "queued" || question.status === "processing";
