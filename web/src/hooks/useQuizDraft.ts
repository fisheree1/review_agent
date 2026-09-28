import { useCallback, useEffect, useRef, useState } from "react";

import { saveQuizAnswer } from "../api/learning";

export type QuizResponseValue = string | string[] | boolean;

export function useQuizDraft(quizId: string, attemptId: string, initialRevision: number) {
  const revision = useRef(initialRevision);
  const pending = useRef(new Map<string, QuizResponseValue>());
  const active = useRef<Promise<void> | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const alive = useRef(true);
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!active.current && pending.current.size === 0) revision.current = initialRevision;
  }, [initialRevision]);

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    if (active.current) return active.current;
    if (!pending.current.size) return;
    if (alive.current) { setStatus("saving"); setError(""); }
    const task = (async () => {
      while (pending.current.size) {
        const [questionId, response] = pending.current.entries().next().value!;
        pending.current.delete(questionId);
        try {
          const result = await saveQuizAnswer(quizId, attemptId, questionId, response, revision.current);
          revision.current = result.revision;
        } catch (caught) {
          if (!pending.current.has(questionId)) pending.current.set(questionId, response);
          throw caught;
        }
      }
      if (alive.current) setStatus("saved");
    })();
    active.current = task;
    try { await task; }
    catch (caught) {
      if (alive.current) { setStatus("error"); setError(caught instanceof Error ? caught.message : "作答保存失败"); }
      throw caught;
    } finally { active.current = null; }
  }, [quizId, attemptId]);

  const edit = (questionId: string, response: QuizResponseValue) => {
    pending.current.set(questionId, response);
    setStatus("saving");
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => { void flush().catch(() => undefined); }, 550);
  };
  useEffect(() => {
    alive.current = true;
    const unload = (event: BeforeUnloadEvent) => {
      if (pending.current.size || active.current) { event.preventDefault(); event.returnValue = ""; }
    };
    const navigate = (event: MouseEvent) => {
      if (!(event.target instanceof Element) || !event.target.closest("a[href]")) return;
      if (pending.current.size || active.current) {
        event.preventDefault();
        void flush().catch(() => undefined);
      }
    };
    window.addEventListener("beforeunload", unload);
    document.addEventListener("click", navigate, true);
    return () => {
      alive.current = false;
      if (timer.current) clearTimeout(timer.current);
      window.removeEventListener("beforeunload", unload);
      document.removeEventListener("click", navigate, true);
      void flush().catch(() => undefined);
    };
  }, [flush]);
  const discard = (latestRevision: number) => {
    if (active.current) return false;
    if (timer.current) clearTimeout(timer.current);
    pending.current.clear();
    revision.current = latestRevision;
    setStatus("idle"); setError("");
    return true;
  };
  return { edit, flush, discard, status, error, revision };
}
