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
  const readyDocuments = documents.filter((document) => document.status === "ready");
  return (
    <fieldset className="scope-picker">
      <legend>{label}</legend>
      <p>最多 5 份资料</p>
      {readyDocuments.length === 0 ? (
        <p>暂无可用资料</p>
      ) : (
        <div className="scope-picker__items">
          {readyDocuments.map((document) => (
            <label key={document.id}>
              <input
                checked={value.document_ids.includes(document.id)}
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
                onChange={() => onChange({ ...value, collection_ids: toggle(value.collection_ids, collection.id) })}
                name="collection_ids"
                value={collection.id}
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
