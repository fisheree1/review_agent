import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { getCurrentUser } from "./api/auth";
import { ApiError } from "./api/documents";
import { ErrorState } from "./components/ErrorState";
import { LibraryPage } from "./pages/LibraryPage";
import { ReaderPage } from "./pages/ReaderPage";
import { StudyPage } from "./pages/StudyPage";
import { QuizPage } from "./pages/QuizPage";
import { QuizAttemptPage } from "./pages/QuizAttemptPage";
import { LoginPage } from "./pages/LoginPage";
import { AccountPage } from "./pages/AccountPage";

export function App() {
  const queryClient = useQueryClient();
  const location = useLocation();
  const auth = useQuery({ queryKey: ["auth-me"], queryFn: getCurrentUser, retry: false });
  useEffect(() => {
    const expire = () => {
      queryClient.setQueryData(["auth-me"], null);
      void queryClient.invalidateQueries({ queryKey: ["auth-me"] });
    };
    window.addEventListener("review-agent-auth-expired", expire);
    return () => window.removeEventListener("review-agent-auth-expired", expire);
  }, [queryClient]);

  if (auth.isPending) return <main className="auth-screen" role="status">正在验证登录状态…</main>;
  if (auth.error && !(auth.error instanceof ApiError && auth.error.status === 401)) {
    return <main className="auth-screen"><ErrorState message="暂时无法验证登录状态，请检查服务后重试。" onRetry={() => void auth.refetch()} /></main>;
  }
  const identity = auth.data ?? null;
  if (location.pathname === "/login") return <LoginPage identity={identity} />;
  if (!identity) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;

  return (
    <Routes>
      <Route path="/" element={<LibraryPage />} />
      <Route path="/documents/:documentId" element={<ReaderPage />} />
      <Route path="/study" element={<StudyPage />} />
      <Route path="/study/:conversationId" element={<StudyPage />} />
      <Route path="/quizzes" element={<QuizPage />} />
      <Route path="/quizzes/:quizId" element={<QuizPage />} />
      <Route path="/quizzes/:quizId/attempts/:attemptId" element={<QuizAttemptPage />} />
      <Route path="/account" element={<AccountPage identity={identity} />} />
      <Route path="*" element={<Navigate replace to="/" />} />
    </Routes>
  );
}
