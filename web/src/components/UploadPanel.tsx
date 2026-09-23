import { useEffect, useRef, useState } from "react";

import { ApiError, uploadDocument } from "../api/documents";
import { Icon } from "./Icon";

const MAX_FILE_SIZE = 25 * 1024 * 1024;
const MAX_BATCH_FILES = 10;
const SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".pptx"];

type UploadStatus = "selected" | "uploading" | "completed" | "failed";

interface UploadItem {
  id: string;
  key: string;
  file: File;
  status: UploadStatus;
  progress: number;
  error: string | null;
  valid: boolean;
}

interface UploadPanelProps {
  onUploaded: (documentId: string) => void;
}

function validateFile(file: File): string | null {
  if (!SUPPORTED_EXTENSIONS.some((extension) => file.name.toLowerCase().endsWith(extension))) {
    return "目前支持 PDF、DOCX 和 PPTX 文件。";
  }
  if (file.size > MAX_FILE_SIZE) return "超过 25 MB，请先压缩或拆分。";
  if (file.size === 0) return "文件为空，请重新选择。";
  return null;
}

export function UploadPanel({ onUploaded }: UploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const uploadControllerRef = useRef<AbortController | null>(null);
  const cancelledRef = useRef(false);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function chooseFiles(files: FileList | File[]) {
    if (isUploading) return;
    const selected = Array.from(files);
    setError(null);
    if (selected.length > MAX_BATCH_FILES) {
      setError(`一次最多选择 ${MAX_BATCH_FILES} 个文件。`);
      return;
    }
    setItems(selected.map((file) => {
      const reason = validateFile(file);
      return {
        id: crypto.randomUUID(), key: crypto.randomUUID(), file,
        status: reason ? "failed" : "selected", progress: 0, error: reason, valid: !reason,
      };
    }));
  }

  async function startUpload() {
    if (isUploading || !items.some((item) => item.valid && item.status !== "completed")) return;
    cancelledRef.current = false;
    setIsUploading(true);
    try {
      for (const item of items) {
        if (cancelledRef.current) break;
        if (!item.valid || item.status === "completed") continue;
        const controller = new AbortController();
        uploadControllerRef.current = controller;
        setItems((current) => current.map((entry) => entry.id === item.id
          ? { ...entry, status: "uploading", progress: 0, error: null } : entry));
        try {
          const document = await uploadDocument(item.file, (progress) => {
            setItems((current) => current.map((entry) => entry.id === item.id
              ? { ...entry, progress } : entry));
          }, controller.signal, item.key);
          setItems((current) => current.map((entry) => entry.id === item.id
            ? { ...entry, status: "completed", progress: 100 } : entry));
          onUploaded(document.id);
        } catch (caught) {
          const message = caught instanceof ApiError ? caught.message : "上传失败，请稍后重试。";
          setItems((current) => current.map((entry) => entry.id === item.id
            ? { ...entry, status: "failed", error: message } : entry));
        } finally {
          uploadControllerRef.current = null;
        }
      }
    } finally {
      setIsUploading(false);
    }
  }

  useEffect(() => () => uploadControllerRef.current?.abort(), []);

  const pendingCount = items.filter((item) => item.valid && item.status !== "completed").length;
  const completedCount = items.filter((item) => item.status === "completed").length;
  return (
    <section aria-labelledby="upload-title" className="upload-panel">
      <div className="upload-panel__copy">
        <span className="eyebrow">建立你的资料库</span>
        <h1 id="upload-title">把学习资料放进来，安静地读完它。</h1>
        <p>一次选择最多 10 份 PDF、DOCX 或 PPTX，每份最大 25 MB。后台会识别扫描 PDF 中的中英文，并保留来源位置。</p>
      </div>
      <div
        className={`drop-zone${isDragging ? " drop-zone--active" : ""}`}
        onDragEnter={(event) => { event.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault(); setIsDragging(false);
          chooseFiles(event.dataTransfer.files);
        }}
      >
        <span className="drop-zone__icon"><Icon name="upload" /></span>
        <div><strong>拖放资料到这里</strong><span>或从电脑选择，最多 10 份</span></div>
        <input
          accept="application/pdf,.pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.docx,application/vnd.openxmlformats-officedocument.presentationml.presentation,.pptx"
          className="visually-hidden" id="document-file" disabled={isUploading} multiple
          onChange={(event) => { if (event.target.files) chooseFiles(event.target.files); event.target.value = ""; }}
          ref={inputRef} type="file"
        />
        <div className="drop-zone__actions">
          <button className="button button--secondary" disabled={isUploading} onClick={() => inputRef.current?.click()} type="button">选择文件</button>
          {pendingCount > 0 ? <button className="button button--primary" disabled={isUploading} onClick={() => void startUpload()} type="button">
            {isUploading ? "上传中…" : completedCount > 0 ? `继续上传剩余 ${pendingCount} 份` : `开始上传 ${pendingCount} 份`}
          </button> : null}
          {isUploading ? <button className="button button--danger-quiet" onClick={() => {
            cancelledRef.current = true;
            uploadControllerRef.current?.abort();
          }} type="button">停止批量上传</button> : null}
        </div>
        {items.length > 0 ? <ul aria-label="上传文件列表" className="upload-file-list">
          {items.map((item) => <li key={item.id}>
            <strong>{item.file.name}</strong><span>{(item.file.size / 1024 / 1024).toFixed(1)} MB</span>
            <span>{item.status === "completed" ? "已上传，后台处理中" : item.status === "uploading" ? `上传中 ${item.progress}%` : item.status === "selected" ? "等待上传" : "上传失败"}</span>
            {item.status === "uploading" ? <progress aria-label={`${item.file.name} 上传进度`} max="100" value={item.progress}>{item.progress}%</progress> : null}
            {item.error ? <span className="field-error" role="alert">{item.error}</span> : null}
          </li>)}
        </ul> : null}
        {completedCount > 0 ? <p role="status">已提交 {completedCount} 份资料，解析会在后台继续。</p> : null}
        {error ? <p className="field-error" role="alert"><Icon name="error" />{error}</p> : null}
      </div>
    </section>
  );
}
