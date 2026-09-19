import { type DocumentPage } from "../api/documents";
import { Icon } from "./Icon";

interface ReferencePanelProps {
  currentPage: number;
  isOpen: boolean;
  pages: DocumentPage[];
  onClose: () => void;
  onSelect: (pageNumber: number) => void;
}

function excerpt(content: string): string {
  const compact = content.replace(/\s+/g, " ").trim();
  return compact.length > 105 ? `${compact.slice(0, 105)}…` : compact || "本页没有可提取文字";
}

export function ReferencePanel({ currentPage, isOpen, pages, onClose, onSelect }: ReferencePanelProps) {
  return (
    <aside aria-label="页码与引用" className={`reference-panel${isOpen ? " reference-panel--open" : ""}`}>
      <div className="reference-panel__header">
        <div><span className="eyebrow">来源定位</span><h2>页码与引用</h2></div>
        <button aria-label="关闭引用面板" className="icon-button reference-panel__close" onClick={onClose} type="button"><Icon name="close" /></button>
      </div>
      <p className="reference-panel__intro">选择一页即可回到对应正文。后续问答引用也会沿用这套页码定位。</p>
      <nav aria-label="文档页码" className="reference-list">
        {pages.map((page) => (
          <button
            aria-current={page.page_number === currentPage ? "location" : undefined}
            className={`reference-item${page.page_number === currentPage ? " reference-item--active" : ""}`}
            key={page.page_number}
            onClick={() => onSelect(page.page_number)}
            type="button"
          >
            <span className="reference-item__page">第 {page.page_number} 页</span>
            <span>{excerpt(page.content)}</span>
          </button>
        ))}
      </nav>
    </aside>
  );
}
