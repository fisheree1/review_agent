import { apiFetch, getCsrfToken } from "./client";

export type DocumentStatus =
  | "uploaded"
  | "queued"
  | "parsing"
  | "ready"
  | "failed"
  | "deleting";

export interface DocumentSummary {
  id: string;
  filename: string;
  media_type: string;
  byte_size: number;
  status: DocumentStatus;
  page_count: number | null;
  content_count: number | null;
  failure_code: string | null;
  failure_message: string | null;
  created_at: string;
  updated_at: string;
}

export type CitationLocatorKind = "page" | "heading" | "slide";

export interface CitationLocator {
  kind: CitationLocatorKind;
  position: number;
  title: string | null;
  path: string[];
}

export interface DocumentContent {
  ordinal: number;
  content: string;
  citation_locator: CitationLocator;
}

export interface DocumentContentLocation {
  ordinal: number;
  citation_locator: CitationLocator;
}

export interface DocumentListResponse {
  items: DocumentSummary[];
  next_cursor: string | null;
}

export interface DocumentContentResponse {
  document_id: string;
  content_count: number;
  contents: DocumentContent[];
  locations: DocumentContentLocation[];
}

interface ErrorEnvelope {
  error?: {
    code?: string;
    message?: string;
  };
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(message: string, code = "REQUEST_FAILED", status = 0) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

export async function parseResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    return (await response.json()) as T;
  }
  let envelope: ErrorEnvelope | undefined;
  try {
    envelope = (await response.json()) as ErrorEnvelope;
  } catch {
    envelope = undefined;
  }
  throw new ApiError(
    envelope?.error?.message ?? "服务暂时不可用，请稍后重试",
    envelope?.error?.code ?? "REQUEST_FAILED",
    response.status,
  );
}

export async function listDocuments(cursor?: string): Promise<DocumentListResponse> {
  const query = new URLSearchParams({ limit: "30" });
  if (cursor) query.set("cursor", cursor);
  const response = await apiFetch(`/api/v1/documents?${query}`, { headers: { Accept: "application/json" } });
  return parseResponse<DocumentListResponse>(response);
}

export async function getDocument(documentId: string): Promise<DocumentSummary> {
  const response = await apiFetch(`/api/v1/documents/${documentId}`, {
    headers: { Accept: "application/json" },
  });
  return parseResponse<DocumentSummary>(response);
}

export async function getDocumentContent(
  documentId: string,
  ordinal: number,
  versionId?: number,
): Promise<DocumentContentResponse> {
  const query = new URLSearchParams({ ordinal: String(ordinal) });
  if (versionId !== undefined) query.set("version_id", String(versionId));
  const response = await apiFetch(`/api/v1/documents/${documentId}/content?${query}`, {
    headers: { Accept: "application/json" },
  });
  return parseResponse<DocumentContentResponse>(response);
}

export async function retryDocument(documentId: string): Promise<DocumentSummary> {
  const response = await apiFetch(`/api/v1/documents/${documentId}:retry`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Idempotency-Key": crypto.randomUUID(),
    },
  });
  return parseResponse<DocumentSummary>(response);
}

export async function deleteDocument(documentId: string): Promise<void> {
  const response = await apiFetch(`/api/v1/documents/${documentId}`, { method: "DELETE" });
  if (!response.ok) await parseResponse<never>(response);
}

export function uploadDocument(
  file: File,
  onProgress: (progress: number) => void,
  signal?: AbortSignal,
  idempotencyKey?: string,
): Promise<DocumentSummary> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/v1/documents");
    request.responseType = "json";
    request.setRequestHeader("Accept", "application/json");
    const csrfToken = getCsrfToken();
    if (csrfToken) request.setRequestHeader("X-CSRF-Token", csrfToken);
    request.setRequestHeader("Idempotency-Key", idempotencyKey ?? crypto.randomUUID());
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100));
    });
    request.addEventListener("load", () => {
      if (request.status >= 200 && request.status < 300) {
        const payload = request.response as { document: DocumentSummary };
        onProgress(100);
        resolve(payload.document);
        return;
      }
      const envelope = request.response as ErrorEnvelope | null;
      reject(
        new ApiError(
          envelope?.error?.message ?? "上传失败，请检查文件后重试",
          envelope?.error?.code ?? "UPLOAD_FAILED",
          request.status,
        ),
      );
    });
    request.addEventListener("error", () => {
      reject(new ApiError("无法连接服务，请确认本地服务已启动", "NETWORK_ERROR"));
    });
    request.addEventListener("abort", () => {
      reject(new ApiError("已停止等待上传结果；如果文件刚好上传完成，请刷新资料列表确认。", "UPLOAD_CANCELLED"));
    });
    signal?.addEventListener("abort", () => request.abort(), { once: true });
    const body = new FormData();
    body.append("file", file);
    request.send(body);
  });
}

export function isProcessing(status: DocumentStatus): boolean {
  return status === "uploaded" || status === "queued" || status === "parsing";
}
