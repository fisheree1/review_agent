import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

export function ConversationMessages({ children, updateKey }: { children: ReactNode; updateKey: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const previous = useRef<string | null>(null);
  const [hasNew, setHasNew] = useState(false);
  const showLatest = () => {
    following.current = true; setHasNew(false);
    viewport.current?.scrollTo({ top: viewport.current.scrollHeight,
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  };
  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element || previous.current === updateKey) return;
    const initial = previous.current === null;
    previous.current = updateKey;
    if (initial || (following.current && !content.current?.contains(document.activeElement))) {
      element.scrollTop = element.scrollHeight;
    } else setHasNew(true);
  }, [updateKey]);
  useLayoutEffect(() => {
    if (!content.current || typeof ResizeObserver === "undefined") return;
    let height = content.current.getBoundingClientRect().height;
    const observer = new ResizeObserver(() => {
      const next = content.current?.getBoundingClientRect().height ?? height;
      if (next > height + 2 && !content.current?.contains(document.activeElement)) {
        if (following.current) {
          if (viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
        } else setHasNew(true);
      }
      height = next;
    });
    observer.observe(content.current);
    return () => observer.disconnect();
  }, []);
  return <>
    <div aria-label="对话记录" className="learning-messages" ref={viewport} role="region" tabIndex={0} onScroll={() => {
      const element = viewport.current!;
      following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
      if (following.current) setHasNew(false);
    }}><div ref={content}>{children}</div></div>
    {hasNew ? <button className="button button--secondary new-replies" onClick={showLatest} type="button">有新回复 · 查看最新</button> : null}
  </>;
}
