import { useEffect, useRef, useState } from "react";
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy } from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";

import { getOriginalPdf } from "../api/documents";

GlobalWorkerOptions.workerSrc = workerUrl;

interface PdfPreviewProps {
  documentId: string;
  page: number;
  onPageCount?: (count: number) => void;
}

export function PdfPreview({ documentId, page, onPageCount }: PdfPreviewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderedLayoutRef = useRef<{ page: number; zoom: number } | null>(null);
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isRendering, setIsRendering] = useState(true);
  const [width, setWidth] = useState(0);
  const [renderedHeight, setRenderedHeight] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const observer = new ResizeObserver(() => setWidth(container.clientWidth));
    observer.observe(container);
    setWidth(container.clientWidth);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    let loadingTask: ReturnType<typeof getDocument> | null = null;
    setPdf(null);
    setError(null);
    setIsRendering(true);
    void (async () => {
      try {
        const data = await getOriginalPdf(documentId, controller.signal);
        if (!active) return;
        loadingTask = getDocument({ data });
        const loaded = await loadingTask.promise;
        if (active) {
          onPageCount?.(loaded.numPages);
          setPdf(loaded);
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "PDF 原文件无法打开");
      }
    })();
    return () => {
      active = false;
      controller.abort();
      void loadingTask?.destroy();
    };
  }, [documentId, onPageCount, retry]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!pdf || !canvas || width <= 0) return;
    let active = true;
    let renderTask: ReturnType<Awaited<ReturnType<PDFDocumentProxy["getPage"]>>["render"]> | null = null;
    setIsRendering(true);
    setError(null);
    void (async () => {
      try {
        const pdfPage = await pdf.getPage(page);
        if (!active) return;
        const base = pdfPage.getViewport({ scale: 1 });
        const viewport = pdfPage.getViewport({ scale: Math.min(width / base.width, 2) * zoom });
        const ratio = Math.min(window.devicePixelRatio || 1, 2);
        const context = canvas.getContext("2d");
        if (!context) throw new Error("浏览器不支持 PDF 页面绘制");
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        renderTask = pdfPage.render({
          canvas,
          canvasContext: context,
          viewport,
          transform: [ratio, 0, 0, ratio, 0, 0],
        });
        await renderTask.promise;
        if (active) {
          // A narrower side panel must not collapse the page and reset the reading position.
          const samePageAndZoom = renderedLayoutRef.current?.page === page && renderedLayoutRef.current.zoom === zoom;
          renderedLayoutRef.current = { page, zoom };
          setRenderedHeight((height) => samePageAndZoom ? Math.max(height, Math.ceil(viewport.height)) : Math.ceil(viewport.height));
          setIsRendering(false);
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "PDF 页面绘制失败");
      }
    })();
    return () => {
      active = false;
      renderTask?.cancel();
    };
  }, [pdf, page, width, zoom]);

  return (
    <div className="pdf-preview">
      <div className="pdf-preview__toolbar">
        <span>PDF 原貌 · 第 {page} 页</span>
        <div className="pdf-preview__zoom" aria-label="PDF 缩放">
          <button aria-label="缩小 PDF" disabled={zoom <= 0.5} onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))} type="button">−</button>
          <span>{Math.round(zoom * 100)}%</span>
          <button aria-label="放大 PDF" disabled={zoom >= 2} onClick={() => setZoom((value) => Math.min(2, value + 0.25))} type="button">+</button>
        </div>
      </div>
      {error ? <div className="pdf-preview__error" role="alert"><p>{error}</p><button className="button button--secondary" onClick={() => setRetry((value) => value + 1)} type="button">重试预览</button></div> : null}
      {isRendering && !error ? <p className="pdf-preview__status" role="status">正在绘制 PDF 页面…</p> : null}
      <div className="pdf-preview__viewport" ref={containerRef} style={{ minHeight: renderedHeight || undefined }}>
        <canvas aria-label={`PDF 原文件第 ${page} 页`} className="pdf-preview__canvas" key={`${page}-${zoom}-${width}`} ref={canvasRef} role="img" style={{ visibility: isRendering || error ? "hidden" : "visible" }} />
      </div>
    </div>
  );
}
