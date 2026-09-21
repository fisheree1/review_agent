import { type CitationLocator, type DocumentSummary } from "./api/documents";

export function citationLabel(locator: CitationLocator): string {
  if (locator.kind === "page") return `第 ${locator.position} 页`;
  if (locator.kind === "slide") {
    return locator.title
      ? `幻灯片 ${locator.position} · ${locator.title}`
      : `幻灯片 ${locator.position}`;
  }
  if (locator.path.length > 0) return locator.path.join(" › ");
  return locator.title ?? `章节 ${locator.position}`;
}

export function documentTypeLabel(mediaType: string): string {
  if (mediaType === "application/pdf") return "PDF";
  if (mediaType.includes("wordprocessingml")) return "DOCX";
  if (mediaType.includes("presentationml")) return "PPTX";
  return "文档";
}

export function contentCountLabel(document: Pick<DocumentSummary, "media_type" | "content_count">): string {
  const count = document.content_count;
  if (!count) return "正在确认内容范围";
  if (document.media_type === "application/pdf") return `${count} 页`;
  if (document.media_type.includes("presentationml")) return `${count} 张幻灯片`;
  return `${count} 个章节`;
}
