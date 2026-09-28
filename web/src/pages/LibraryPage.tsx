import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useSearchParams } from "react-router-dom";

import { createCollection, listCollections } from "../api/learning";
import { ContinueLearning } from "../components/ContinueLearning";
import { StudyCalendar } from "../components/StudyCalendar";

import { AppHeader } from "../components/AppHeader";
import { DocumentList } from "../components/DocumentList";
import { ErrorState } from "../components/ErrorState";
import { SelectField } from "../components/SelectField";
import { UploadPanel } from "../components/UploadPanel";
import { useDocuments } from "../hooks/useDocuments";

const statusOptions = [
  { value: "all", label: "全部状态" }, { value: "ready", label: "可以阅读" },
  { value: "processing", label: "处理中" }, { value: "failed", label: "处理失败" },
] as const;
const sortOptions = [{ value: "newest", label: "最新上传" }, { value: "oldest", label: "最早上传" }] as const;

export function LibraryPage() {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const query = params.get("search") ?? "";
  const status = ["ready", "processing", "failed"].includes(params.get("status") ?? "") ? params.get("status")! : "all";
  const sort = params.get("sort") === "oldest" ? "oldest" : "newest";
  const collectionId = params.get("collection") ?? "";
  const [search, setSearch] = useState(query);
  useEffect(() => { const timer = window.setTimeout(() => setSearch(query), 250); return () => clearTimeout(timer); }, [query]);
  useEffect(() => {
    if (location.hash !== "#upload-materials") return;
    const upload = document.getElementById("upload-materials");
    upload?.scrollIntoView({ block: "start" });
    upload?.focus({ preventScroll: true });
  }, [location.hash]);
  const { documents, error, isLoading, isLoadingMore, nextCursor, refresh, loadMore } = useDocuments({
    search: search.trim(), status: status === "all" ? undefined : status, sort, collection_id: collectionId || undefined,
  });
  const collections = useQuery({ queryKey: ["collections"], queryFn: listCollections });
  const cache = useQueryClient();
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [collectionName, setCollectionName] = useState("");
  const batch = useMutation({ mutationFn: () => createCollection(collectionName.trim(), "", selected),
    onSuccess: () => { setSelected([]); setSelecting(false); setCollectionName(""); void cache.invalidateQueries({ queryKey: ["collections"] }); },
  });
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params); if (value) next.set(key, value); else next.delete(key);
    setParams(next, { replace: true }); setSelected([]); batch.reset();
  };
  const filtering = Boolean(query || status !== "all" || collectionId);

  return (
    <div className="app-page">
      <AppHeader />
      <main className="library" id="main-content" tabIndex={-1}>
        <header className="learning-heading page-heading"><h1>资料库</h1>
          <Link className="button button--secondary" to="/study">进入学习空间</Link>
        </header>
        <div className="library-layout">
        <div className="library-upload"><UploadPanel onUploaded={() => void refresh()} /></div>
        <section aria-labelledby="library-title" className="library-section">
          <div className="section-heading">
            <h2 id="library-title">全部资料</h2>
            <p>{documents.length > 0 ? `${documents.length} 份资料` : "资料会按最近上传排序"}</p>
          </div>
          <div className="library-toolbar">
            <div className="field"><label htmlFor="library-search">搜索全部资料</label><input id="library-search" name="library-search" autoComplete="off" type="search" maxLength={160} placeholder="输入文件名…" value={query} onChange={(event) => setFilter("search", event.target.value)} /></div>
            <div className="field"><SelectField id="library-status" name="library-status" label="处理状态" value={status} onChange={(value) => setFilter("status", value === "all" ? "" : value)} options={statusOptions} /></div>
            {filtering ? <button className="button button--secondary" onClick={() => { setParams({}, { replace: true }); setSelected([]); }} type="button">清除筛选</button> : null}
          </div>
          <details className="library-tools"><summary>更多筛选与批量操作</summary>
            <div className="library-toolbar">
              <div className="field"><SelectField id="library-sort" name="library-sort" label="上传顺序" value={sort} onChange={(value) => setFilter("sort", value)} options={sortOptions} /></div>
              <div className="field"><SelectField id="library-collection" name="library-collection" label="资料集合" value={collectionId} onChange={(value) => setFilter("collection", value)} options={[{ value: "", label: "全部集合" }, ...(collections.data ?? []).map((collection) => ({ value: collection.id, label: collection.name }))]} /></div>
              <button className="button button--secondary" type="button" disabled={batch.isPending} onClick={() => { setSelecting(!selecting); setSelected([]); batch.reset(); }}>{selecting ? "退出批量选择" : "批量选择"}</button>
            </div>
            {collections.error ? <p role="alert">无法读取集合。<button className="button button--secondary" type="button" onClick={() => void collections.refetch()}>重试读取集合</button></p> : null}
          </details>
          {selecting ? <form className="batch-collection" onSubmit={(event) => { event.preventDefault(); if (collectionName.trim() && selected.length) batch.mutate(); }}>
            <span>已选 {selected.length} / 100 份</span><label htmlFor="batch-collection-name">新集合名称</label><input id="batch-collection-name" name="batch-collection-name" autoComplete="off" disabled={batch.isPending} maxLength={120} value={collectionName} onChange={(event) => setCollectionName(event.target.value)} />
            <button className="button button--primary" disabled={!selected.length || !collectionName.trim() || batch.isPending} type="submit">将所选资料建为集合</button>
            {batch.error ? <p role="alert">{batch.error.message}，选择已保留，可重试。</p> : null}
          </form> : null}
          {batch.isSuccess ? <p role="status">集合已建立，可在学习空间选择它。</p> : null}
          {!isLoading ? <p className="results-count" role="status">{search !== query ? "正在更新搜索…" : `已显示 ${documents.length} 份${filtering ? "匹配" : ""}资料${nextCursor ? "，可继续加载更多" : ""}`}</p> : null}

          <div className="library-results" role="region" aria-label="资料列表" tabIndex={0}>
          {error && documents.length === 0 ? (
            <ErrorState message={error.message} onRetry={() => void refresh()} />
          ) : (
            <DocumentList
              documents={documents}
              selectedIds={selecting ? selected : undefined}
              selectionDisabled={batch.isPending}
              onToggleSelection={(id) => setSelected((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length < 100 ? [...current, id] : current)}
              emptyTitle={filtering ? "没有匹配的资料" : undefined}
              emptyDescription={filtering ? "尝试其他文件名，或清除筛选。" : undefined}
              hasMore={nextCursor !== null}
              isLoading={isLoading}
              isLoadingMore={isLoadingMore}
              onLoadMore={() => void loadMore()}
            />
          )}

          {error && documents.length > 0 ? (
            <p className="inline-notice" role="status">资料状态暂时未能刷新：{error.message} <button className="button button--secondary" type="button" onClick={() => void refresh()}>重试刷新</button></p>
          ) : null}
          </div>
        </section>
        <aside className="library-utilities" aria-label="学习工具">
          <ContinueLearning />
          <StudyCalendar />
        </aside>
        </div>
      </main>
    </div>
  );
}
