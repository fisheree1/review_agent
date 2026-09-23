import type { DocumentSummary } from "../api/documents";
import type { Collection, ScopeChoice } from "../api/learning";

interface Props {
  documents: DocumentSummary[];
  collections: Collection[];
  value: ScopeChoice;
  onChange: (value: ScopeChoice) => void;
  label: string;
}

function toggle(items: string[], id: string): string[] {
  return items.includes(id) ? items.filter((item) => item !== id) : [...items, id];
}

export function ScopePicker({ documents, collections, value, onChange, label }: Props) {
  return (
    <fieldset className="scope-picker">
      <legend>{label}</legend>
      <p>最多选择 5 份已准备索引的资料。集合内资料也计入上限。</p>
      {documents.filter((document) => document.status === "ready").length === 0 ? (
        <p>还没有可用资料。请先上传并完成资料问答索引。</p>
      ) : (
        <div className="scope-picker__items">
          {documents.filter((document) => document.status === "ready").map((document) => (
            <label key={document.id}>
              <input
                checked={value.document_ids.includes(document.id)}
                onChange={() => onChange({ ...value, document_ids: toggle(value.document_ids, document.id) })}
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
                onChange={() => onChange({ ...value, collection_ids: toggle(value.collection_ids, collection.id) })}
                type="checkbox"
              />
              <span>{collection.name}（{collection.document_ids.length} 份）</span>
            </label>
          ))}
        </div>
      ) : null}
    </fieldset>
  );
}
