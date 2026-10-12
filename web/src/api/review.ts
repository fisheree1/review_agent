import { apiFetch } from "./client";
import { parseResponse } from "./documents";
import type { QuizQuestion } from "./learning";

export type ReviewRating = "again" | "hard" | "good";
export interface ReviewCard {
  id: string; due_at: string; revision: number; review_count: number; question: QuizQuestion;
}
export interface ReviewQueue {
  due_count: number; upcoming_count: number; items: ReviewCard[];
  upcoming: { id: string; due_at: string; topic: string; interval_days: number }[];
}
export interface ReviewResult { id: string; due_at: string; interval_days: number; revision: number }
export const getReviewQueue = async () => parseResponse<ReviewQueue>(await apiFetch("/api/v1/learning/review"));
export const revealReviewAnswer = async (id: string) => parseResponse<QuizQuestion>(await apiFetch(`/api/v1/learning/review/${id}/answer`));
export const enrollReview = async (quiz: string, attempt: string) => parseResponse<{ added: number; existing: number }>(await apiFetch(`/api/v1/quizzes/${quiz}/attempts/${attempt}/review-cards`, { method: "POST" }));
export const rateReview = async (card: ReviewCard, rating: ReviewRating, key: string) => parseResponse<ReviewResult>(await apiFetch(`/api/v1/learning/review/${card.id}:rate`, {
  method: "POST", headers: { "Content-Type": "application/json", "Idempotency-Key": key },
  body: JSON.stringify({ rating, expected_revision: card.revision }),
}));
