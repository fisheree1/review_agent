import { Navigate, Route, Routes } from "react-router-dom";

import { LibraryPage } from "./pages/LibraryPage";
import { ReaderPage } from "./pages/ReaderPage";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<LibraryPage />} />
      <Route path="/documents/:documentId" element={<ReaderPage />} />
      <Route path="*" element={<Navigate replace to="/" />} />
    </Routes>
  );
}
