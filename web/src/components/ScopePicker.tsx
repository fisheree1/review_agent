import { Link } from "react-router-dom";

import type { DocumentSummary } from "../api/documents";
import type { Collection, ScopeChoice } from "../api/learning";

interface Props {
  documents: DocumentSummary[];
  collections: Collection[];
  value: ScopeChoice;
  onChange: (value: ScopeChoice) => void;
  label: string;
  loading?: boolean;
  hasMore?: boolean;
  selectedLabels?: readonly { id: string; filename: string }[];
}

function toggle(items: string[], id: string): string[] {
  return items.includes(id) ? items.filter((item) => item !== id) : [...items, id];
}

export function ScopePicker({ documents, collections, value, onChange, label, loading = false, hasMore = false, selectedLabels = [] }: Props) {
  const readyDocuments = documents.filter((document) => document.status === "ready");
  const visibleDocuments = [
    ...readyDocuments.map(({ id, filename }) => ({ id, filename })),
    ...selectedLabels.filter(({ id }) => value.document_ids.includes(id) && !readyDocuments.some((document) => document.id === id)),
  ];
  const selectedDocuments = new Set([
    ...value.document_ids,
    ...collections.filter((collection) => value.collection_ids.includes(collection.id)).flatMap((collection) => collection.document_ids),
  ]);
  const canAddDocument = (id: string) => value.document_ids.length < 5 && (selectedDocuments.has(id) || selectedDocuments.size < 5);
  const canAddCollection = (ids: string[]) => value.collection_ids.length < 5 && new Set([...selectedDocuments, ...ids]).size <= 5;
  return (
    <fieldset className="scope-picker">
      <legend>{label}</legend>
      <p role="status">已选 {selectedDocuments.size} / 5 份资料{selectedDocuments.size >= 5 ? "；先取消一份，才能选择其他资料" : ""}</p>
      {visibleDocuments.length === 0 ? (
        loading ? <p>正在读取可用资料…</p> : documents.length || hasMore
          ? <p>当前没有可用资料。{hasMore ? "可继续加载更多资料；" : ""}<Link to="/">查看资料状态</Link></p>
          : <p>暂无可用资料。<Link to="/#upload-materials">前往资料库上传 PDF</Link></p>
      ) : (
        <div className="scope-picker__items">
          {visibleDocuments.map((document) => (
            <label key={document.id}>
              <input
                checked={value.document_ids.includes(document.id)}
                disabled={!value.document_ids.includes(document.id) && !canAddDocument(document.id)}
                onChange={() => onChange({ ...value, document_ids: toggle(value.document_ids, document.id) })}
                name="document_ids"
                value={document.id}
                type="checkbox"
              />
              <span>{document.filename}</span>
            </label>
          ))}
        </div>
      )}
      {collections.length > 0 ? (
        <div className="scope-picker__items">
          <strong>资料集合</strong>
          {collections.map((collection) => (
            <label key={collection.id}>
              <input
                checked={value.collection_ids.includes(collection.id)}
                disabled={!value.collection_ids.includes(collection.id) && (!collection.document_ids.length || !canAddCollection(collection.document_ids))}
                onChange={() => onChange({ ...value, collection_ids: toggle(value.collection_ids, collection.id) })}
                name="collection_ids"
                value={collection.id}
                type="checkbox"
              />
              <span>{collection.name}（{collection.document_ids.length} 份）{!value.collection_ids.includes(collection.id) && collection.document_ids.length > 0 && !canAddCollection(collection.document_ids) ? " · 超出 5 份上限" : ""}</span>
            </label>
          ))}
        </div>
      ) : null}
    </fieldset>
  );
}
