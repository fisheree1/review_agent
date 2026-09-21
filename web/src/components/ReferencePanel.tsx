import { useCallback, useEffect, useRef } from "react";

import { type DocumentContent, type DocumentContentLocation } from "../api/documents";
import { citationLabel } from "../citations";
import { useFocusTrap } from "../hooks/useFocusTrap";
import { Icon } from "./Icon";

interface ReferencePanelProps {
  currentOrdinal: number;
  isOpen: boolean;
  contentCount: number;
  contents: DocumentContent[];
  locations: DocumentContentLocation[];
  onClose: () => void;
  onSelect: (ordinal: number) => void;
}

function excerpt(content: string): string {
  const compact = content.replace(/\s+/g, " ").trim();
  return compact.length > 105 ? `${compact.slice(0, 105)}…` : compact || "此处没有可提取文字";
}

export function ReferencePanel({
  currentOrdinal,
  isOpen,
  contentCount,
  contents,
  locations,
  onClose,
  onSelect,
}: ReferencePanelProps) {
  const current = contents.find((content) => content.ordinal === currentOrdinal);
  const panelRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  const close = useCallback(() => onCloseRef.current(), []);
  const isModal = isOpen && window.matchMedia("(max-width: 1099px)").matches;
  useFocusTrap(panelRef, isModal, close);
  return (
    <aside
      aria-label="来源定位"
      aria-modal={isModal || undefined}
      className={`reference-panel${isOpen ? " reference-panel--open" : ""}`}
      ref={panelRef}
      role={isModal ? "dialog" : undefined}
    >
      <div className="reference-panel__header">
        <div><span className="eyebrow">Citation locator</span><h2>来源定位</h2></div>
        <button aria-label="关闭引用面板" className="icon-button reference-panel__close" onClick={onClose} type="button"><Icon name="close" /></button>
      </div>
      <p className="reference-panel__intro">
        {current ? `${citationLabel(current.citation_locator)}：${excerpt(current.content)}` : "选择一个来源位置查看正文。"}
      </p>
      <nav aria-label="文档来源位置" className="reference-list">
        {Array.from({ length: contentCount }, (_, index) => index + 1).map((ordinal) => {
          const item = contents.find((content) => content.ordinal === ordinal);
          const location = locations.find((candidate) => candidate.ordinal === ordinal);
          const active = ordinal === currentOrdinal;
          return (
            <button
              aria-current={active ? "location" : undefined}
              className={`reference-item${active ? " reference-item--active" : ""}`}
              key={ordinal}
              onClick={() => onSelect(ordinal)}
              type="button"
            >
              <span className="reference-item__page">
                {location ? citationLabel(location.citation_locator) : `来源 ${ordinal}`}
              </span>
              <span>{active && item ? excerpt(item.content) : "打开此处"}</span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}
