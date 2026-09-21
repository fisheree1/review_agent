import { useEffect, useRef } from "react";

interface DeleteDocumentDialogProps {
  error: string | null;
  filename: string;
  isDeleting: boolean;
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
}

export function DeleteDocumentDialog({
  error,
  filename,
  isDeleting,
  isOpen,
  onClose,
  onConfirm,
}: DeleteDocumentDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (isOpen && !dialog.open) dialog.showModal();
    if (!isOpen && dialog.open) dialog.close();
  }, [isOpen]);

  return (
    <dialog
      aria-describedby="delete-document-description"
      className="confirm-dialog"
      onCancel={(event) => {
        event.preventDefault();
        if (!isDeleting) onClose();
      }}
      onClose={onClose}
      ref={dialogRef}
    >
      <span className="eyebrow">删除资料</span>
      <h2>永久删除这份资料？</h2>
      <p id="delete-document-description">
        “{filename}”的原文件、解析正文与处理记录将从可用资料中移除。此操作无法撤销。
      </p>
      {error ? <p className="field-error" role="alert">{error}</p> : null}
      <div className="confirm-dialog__actions">
        <button className="button button--secondary" disabled={isDeleting} onClick={onClose} type="button">
          保留资料
        </button>
        <button className="button button--danger" disabled={isDeleting} onClick={onConfirm} type="button">
          {isDeleting ? "正在删除…" : "确认删除"}
        </button>
      </div>
    </dialog>
  );
}
