import type { ConversationMessage } from "../api/learning";
import type { Citation } from "../api/rag";
import { citationLabel } from "../citations";

export interface StudySection {
  key: string;
  title: string;
  paragraphs: string[];
  sources: { citation: Citation; filename: string }[];
}

export function studySections(message: ConversationMessage): StudySection[] {
  if (!message.answer) return [];
  const sourcesFor = (citations: Citation[]) => Array.from(new Map(citations.map((citation) => [citation.source_id, {
    citation, filename: message.scope.find((item) => item.document_id === citation.document_id)?.filename ?? "原文",
  }])).values());
  const occurrences = new Map<string, number>();
  const sections = message.answer.claims.map((claim, index) => {
    // A later overview batch can insert earlier pages without changing this selection.
    const identity = `${message.id}-${encodeURIComponent(claim.text)}-${Array.from(new Set(claim.citations.map((citation) => citation.document_id ?? ""))).sort().join("-")}`;
    const occurrence = occurrences.get(identity) ?? 0;
    occurrences.set(identity, occurrence + 1);
    return {
      key: `${identity}-${occurrence}`,
      title: claim.title || `知识点 ${index + 1}`,
      paragraphs: [claim.text, ...(claim.explanation?.split("\n").filter((part) => part.trim()) ?? [])],
      sources: sourcesFor(claim.citations),
    };
  });
  // Historical prose has no per-paragraph mapping: retain it intact with all answer sources.
  if (message.answer.explanation) sections.push({
    key: `${message.id}-explanation`, title: "详细讲解",
    paragraphs: message.answer.explanation.split("\n").filter((part) => part.trim()),
    sources: sourcesFor(message.answer.claims.flatMap((claim) => claim.citations)),
  });
  return sections;
}

export function StudyAnswer({ sections, activeKey, onSelect }: {
  sections: StudySection[]; activeKey?: string; onSelect: (section: StudySection) => void;
}) {
  if (!sections.length) return null;
  return <div className="study-answer">
    {sections.length > 1 ? <details className="study-outline"><summary>知识点目录 · {sections.length}</summary>
      <nav aria-label="本次回答的知识点">{sections.map((section, index) => <a href={`#knowledge-${section.key}`} key={section.key}
        onClick={(event) => {
          event.preventDefault();
          document.getElementById(`knowledge-${section.key}`)?.scrollIntoView({ block: "start", behavior: "instant" });
          onSelect(section);
        }}>{index + 1}. {section.title}</a>)}</nav>
    </details> : null}
    {sections.map((section) => <section className="study-knowledge" data-knowledge-key={section.key} data-active={activeKey === section.key}
      id={`knowledge-${section.key}`} key={section.key} aria-label={section.title}>
      <h4><button aria-pressed={activeKey === section.key} className="study-knowledge__select" onClick={() => onSelect(section)} type="button">
        {section.title}{" "}<span>查看原文</span>
      </button></h4>
      {section.paragraphs.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
      <span className="study-knowledge__pages">{Array.from(new Set(section.sources.map(({ citation, filename }) => `${filename} · ${citationLabel(citation.locator)}`))).join(" / ")}</span>
    </section>)}
  </div>;
}
