import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { getOriginalPdf } from "../api/documents";

GlobalWorkerOptions.workerSrc = workerUrl;

interface PdfPreviewProps {
  documentId: string;
  page: number;
  continuous?: boolean;
  onPageCount?: (count: number) => void;
  onPageChange?: (page: number) => void;
}

function PdfCanvasPage({ pdf, page, width, zoom, continuous, aspectRatio, selected }: {
  pdf: PDFDocumentProxy; page: number; width: number; zoom: number; continuous: boolean; aspectRatio: number; selected: boolean;
}) {
  const host = useRef<HTMLElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [nearby, setNearby] = useState(!continuous);
  const shouldRender = nearby || selected;
  const [rendering, setRendering] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [height, setHeight] = useState<number | null>(null);

  useEffect(() => {
    if (!continuous || !host.current) return;
    const observer = new IntersectionObserver(([entry]) => setNearby(entry.isIntersecting), { rootMargin: "1000px 0px" });
    observer.observe(host.current);
    return () => observer.disconnect();
  }, [continuous]);

  useEffect(() => {
    if (!shouldRender || width <= 0 || !canvas.current) return;
    let active = true;
    let renderTask: ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["render"]> | null = null;
    setRendering(true);
    setError(null);
    void (async () => {
      try {
        const pdfPage = await pdf.getPage(page);
        if (!active || !canvas.current) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const viewport = pdfPage.getViewport({ scale: Math.min(width / base.width, 2) * zoom });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const context = canvas.current.getContext("2d");
        if (!context) throw new Error("浏览器不支持 PDF 页面绘制");
        canvas.current.width = Math.floor(viewport.width * ratio);
        canvas.current.height = Math.floor(viewport.height * ratio);
        canvas.current.style.width = `${viewport.width}px`;
        canvas.current.style.height = `${viewport.height}px`;
        setHeight(Math.ceil(viewport.height));
        renderTask = pdfPage.render({ canvas: canvas.current, canvasContext: context, viewport,
          transform: [ratio, 0, 0, ratio, 0, 0] });
        await renderTask.promise;
        if (active) setRendering(false);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "PDF 页面绘制失败");
      }
    })();
    return () => { active = false; renderTask?.cancel(); };
  }, [pdf, page, width, zoom, shouldRender]);

  return <section aria-label={`第 ${page} 页`} className="pdf-preview__page" id={continuous ? `content-${page}` : undefined} ref={host} tabIndex={continuous ? -1 : undefined}>
    {continuous ? <span className="pdf-preview__page-number">{page}</span> : null}
    <div className="pdf-preview__paper" style={{ height: height ?? Math.ceil(width / aspectRatio * zoom) }}>
      {error ? <p className="pdf-preview__error" role="alert">第 {page} 页无法显示：{error}</p> : null}
      {shouldRender ? <canvas aria-label={`PDF 原文件第 ${page} 页`} className="pdf-preview__canvas" ref={canvas} role="img" style={{ visibility: rendering || error ? "hidden" : "visible" }} /> : null}
    </div>
  </section>;
}

export function PdfPreview({ documentId, page, continuous = false, onPageCount, onPageChange }: PdfPreviewProps) {
  const container = useRef<HTMLDivElement>(null);
  const onPageChangeRef = useRef(onPageChange);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [width, setWidth] = useState(0);
  const [aspectRatio, setAspectRatio] = useState(0.71);
  const [zoom, setZoom] = useState(1);
  const [retry, setRetry] = useState(0);
  onPageChangeRef.current = onPageChange;

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    setWidth(element.clientWidth);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let loadingTask: ReturnType<typeof getDocument> | null = null;
    setPdf(null);
    setError(null);
    void (async () => {
      try {
        const data = await getOriginalPdf(documentId, controller.signal);
        if (!active) return;
        loadingTask = getDocument({ data });
        const loaded = await loadingTask.promise;
        if (!active) return;
        const firstPage = await loaded.getPage(1);
        if (!active) return;
        const viewport = firstPage.getViewport({ scale: 1 });
        setAspectRatio(viewport.width / viewport.height);
        onPageCount?.(loaded.numPages);
        setPdf(loaded);
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "PDF 原文件无法打开");
      }
    })();
    return () => { active = false; controller.abort(); void loadingTask?.destroy(); };
  }, [documentId, onPageCount, retry]);

  useEffect(() => {
    if (!continuous || !pdf || page <= 1) return;
    const id = window.requestAnimationFrame(() => container.current?.querySelector(`#content-${Math.min(page, pdf.numPages)}`)?.scrollIntoView({ block: "start", behavior: "instant" }));
    return () => window.cancelAnimationFrame(id);
    // Scrolling updates the URL; only the initial PDF load should move to a deep-linked page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [continuous, pdf]);

  useEffect(() => {
    if (!continuous || !pdf) return;
    let frame = 0;
    const update = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const pages = container.current?.querySelectorAll<HTMLElement>(".pdf-preview__page");
        if (!pages?.length) return;
        const center = window.innerHeight * 0.43;
        let nearest = 1;
        let distance = Infinity;
        pages.forEach((element, index) => {
          const rect = element.getBoundingClientRect();
          const delta = center < rect.top ? rect.top - center : center > rect.bottom ? center - rect.bottom : 0;
          if (delta < distance) { distance = delta; nearest = index + 1; }
        });
        onPageChangeRef.current?.(nearest);
      });
    };
    window.addEventListener("scroll", update, { passive: true });
    return () => { window.removeEventListener("scroll", update); window.cancelAnimationFrame(frame); };
  }, [continuous, pdf]);

  const pages = pdf ? continuous ? Array.from({ length: pdf.numPages }, (_, index) => index + 1) : [Math.min(Math.max(page, 1), pdf.numPages)] : [];
  return <div className={`pdf-preview${continuous ? " pdf-preview--continuous" : ""}`} ref={container}>
    <div aria-label="PDF 缩放" className="pdf-preview__toolbar">
      <button aria-label="缩小 PDF" disabled={zoom <= 0.5} onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))} type="button">−</button>
      <span>{Math.round(zoom * 100)}%</span>
      <button aria-label="放大 PDF" disabled={zoom >= 2} onClick={() => setZoom((value) => Math.min(2, value + 0.25))} type="button">+</button>
    </div>
    {error ? <div className="pdf-preview__error" role="alert"><p>{error}</p><button className="button button--secondary" onClick={() => setRetry((value) => value + 1)} type="button">重试预览</button></div> : null}
    {!pdf && !error ? <p className="pdf-preview__status" role="status">正在打开 PDF…</p> : null}
    <div className="pdf-preview__pages">{pdf && width > 0 ? pages.map((number) => <PdfCanvasPage aspectRatio={aspectRatio} continuous={continuous} key={number} page={number} pdf={pdf} selected={!continuous || number === page} width={Math.min(width, 820)} zoom={zoom} />) : null}</div>
  </div>;
}
