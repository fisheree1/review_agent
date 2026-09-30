import { lazy, Suspense, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { getCurrentUser } from "./api/auth";
import { setCsrfToken } from "./api/client";
import { ApiError } from "./api/documents";
import { ErrorState } from "./components/ErrorState";
import { LibraryPage } from "./pages/LibraryPage";
import { PageLoadBoundary, PageLoading } from "./components/PageLoadBoundary";

const ReaderPage = lazy(() => import("./pages/ReaderPage").then((module) => ({ default: module.ReaderPage })));
const StudyPage = lazy(() => import("./pages/StudyPage").then((module) => ({ default: module.StudyPage })));
const QuizPage = lazy(() => import("./pages/QuizPage").then((module) => ({ default: module.QuizPage })));
const QuizAttemptPage = lazy(() => import("./pages/QuizAttemptPage").then((module) => ({ default: module.QuizAttemptPage })));
const LoginPage = lazy(() => import("./pages/LoginPage").then((module) => ({ default: module.LoginPage })));
const AccountPage = lazy(() => import("./pages/AccountPage").then((module) => ({ default: module.AccountPage })));

export function App() {
  const queryClient = useQueryClient();
  const location = useLocation();
  const auth = useQuery({ queryKey: ["auth-me"], queryFn: getCurrentUser, retry: false });
  useEffect(() => {
    const expire = () => {
      setCsrfToken(null);
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth-me" });
      queryClient.setQueryData(["auth-me"], null);
      void queryClient.invalidateQueries({ queryKey: ["auth-me"] });
    };
    window.addEventListener("review-agent-auth-expired", expire);
    return () => window.removeEventListener("review-agent-auth-expired", expire);
  }, [queryClient]);

  if (auth.isPending) return <main className="auth-screen" id="main-content" tabIndex={-1} role="status">正在验证登录状态…</main>;
  if (auth.error && !(auth.error instanceof ApiError && auth.error.status === 401)) {
    return <main className="auth-screen" id="main-content" tabIndex={-1}><ErrorState message="暂时无法验证登录状态，请检查服务后重试。" onRetry={() => void auth.refetch()} /></main>;
  }
  const identity = auth.data ?? null;
  if (location.pathname === "/login") return <PageLoadBoundary key="login"><Suspense fallback={<PageLoading authenticated={false} />}><LoginPage identity={identity} /></Suspense></PageLoadBoundary>;
  if (!identity) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;

  return (
    <PageLoadBoundary key={location.pathname}><Suspense fallback={<PageLoading />}><Routes>
      <Route path="/" element={<LibraryPage />} />
      <Route path="/documents/:documentId" element={<ReaderPage />} />
      <Route path="/study" element={<StudyPage />} />
      <Route path="/study/:conversationId" element={<StudyPage />} />
      <Route path="/quizzes" element={<QuizPage />} />
      <Route path="/quizzes/:quizId" element={<QuizPage />} />
      <Route path="/quizzes/:quizId/attempts/:attemptId" element={<QuizAttemptPage />} />
      <Route path="/calendar" element={<Navigate replace to="/" />} />
      <Route path="/account" element={<AccountPage identity={identity} />} />
      <Route path="*" element={<Navigate replace to="/" />} />
    </Routes></Suspense></PageLoadBoundary>
  );
}
