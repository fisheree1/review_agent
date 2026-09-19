import { useRef, useState } from "react";

import { ApiError, uploadDocument } from "../api/documents";
import { Icon } from "./Icon";

const MAX_FILE_SIZE = 25 * 1024 * 1024;

interface UploadPanelProps {
  onUploaded: (documentId: string) => void;
}

export function UploadPanel({ onUploaded }: UploadPanelProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [isDragging, setIsDragging] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  function chooseFile(nextFile: File | undefined) {
    setError(null);
    setProgress(null);
    if (!nextFile) return;
    if (nextFile.type !== "application/pdf" && !nextFile.name.toLowerCase().endsWith(".pdf")) {
      setFile(null);
      setError("目前仅支持 PDF 文件。请选择扩展名为 .pdf 的资料。");
      return;
    }
    if (nextFile.size > MAX_FILE_SIZE) {
      setFile(null);
      setError("文件超过 25 MB，请先压缩或拆分后再上传。");
      return;
    }
    setFile(nextFile);
  }

  async function startUpload() {
    if (!file || progress !== null) return;
    setError(null);
    setProgress(0);
    try {
      const document = await uploadDocument(file, setProgress);
      onUploaded(document.id);
    } catch (caught) {
      setProgress(null);
      setError(caught instanceof ApiError ? caught.message : "上传失败，请稍后重试。");
    }
  }

  const isUploading = progress !== null;
  return (
    <section aria-labelledby="upload-title" className="upload-panel">
      <div className="upload-panel__copy">
        <span className="eyebrow">建立你的资料库</span>
        <h1 id="upload-title">把学习资料放进来，安静地读完它。</h1>
        <p>上传文本型 PDF。系统会在后台保留页码、提取正文，并把处理状态清楚地告诉你。</p>
      </div>
      <div
        className={`drop-zone${isDragging ? " drop-zone--active" : ""}`}
        onDragEnter={(event) => { event.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDragOver={(event) => event.preventDefault()}
        onDrop={(event) => {
          event.preventDefault();
          setIsDragging(false);
          chooseFile(event.dataTransfer.files[0]);
        }}
      >
        <span className="drop-zone__icon"><Icon name="upload" /></span>
        {file ? (
          <div className="drop-zone__selection">
            <strong>{file.name}</strong>
            <span>{(file.size / 1024 / 1024).toFixed(1)} MB</span>
          </div>
        ) : (
          <div>
            <strong>拖放 PDF 到这里</strong>
            <span>或从电脑选择，最大 25 MB</span>
          </div>
        )}
        <input
          accept="application/pdf,.pdf"
          className="visually-hidden"
          id="document-file"
          onChange={(event) => chooseFile(event.target.files?.[0])}
          ref={inputRef}
          type="file"
        />
        <div className="drop-zone__actions">
          <label className="button button--secondary" htmlFor="document-file">选择文件</label>
          {file ? (
            <button className="button button--primary" disabled={isUploading} onClick={() => void startUpload()} type="button">
              {isUploading ? "上传中…" : "开始上传"}
            </button>
          ) : null}
        </div>
        {isUploading ? (
          <div aria-live="polite" className="upload-progress">
            <div className="upload-progress__labels"><span>正在安全上传</span><span>{progress}%</span></div>
            <progress max="100" value={progress}>{progress}%</progress>
            <span>上传后可离开此页，正文会在后台继续解析。</span>
          </div>
        ) : null}
        {error ? <p className="field-error" role="alert"><Icon name="error" />{error}</p> : null}
      </div>
    </section>
  );
}
