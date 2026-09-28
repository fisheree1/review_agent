import { useCallback, useEffect, useRef } from "react";

import { citationLabel } from "../citations";
import { type Citation } from "../api/rag";
import { useFocusTrap } from "../hooks/useFocusTrap";
import { Icon } from "./Icon";

interface ReferencePanelProps {
  answerCitation: Citation;
  isCompact: boolean;
  isOpen: boolean;
  onClose: () => void;
  onOpenCitation: (citation: Citation) => void;
}

export function ReferencePanel({
  answerCitation,
  isCompact,
  isOpen,
  onClose,
  onOpenCitation,
}: ReferencePanelProps) {
  const panelRef = useRef<HTMLElement>(null);
  const onCloseRef = useRef(onClose);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  const close = useCallback(() => onCloseRef.current(), []);
  const isModal = isOpen && isCompact;
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
        <h2>来源定位</h2>
        <button aria-label="关闭引用面板" className="icon-button reference-panel__close" onClick={onClose} type="button"><Icon name="close" /></button>
      </div>
      <section className="citation-preview" aria-label="回答引用原文">
        <h3>{citationLabel(answerCitation.locator)}</h3>
        <blockquote>{answerCitation.quote}</blockquote>
        <button className="button button--secondary" onClick={() => onOpenCitation(answerCitation)} type="button">在原文中打开</button>
      </section>
    </aside>
  );
}
