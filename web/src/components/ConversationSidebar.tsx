import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  createConversationGroup, deleteConversation, deleteConversationGroup, listConversationGroups,
  renameConversationGroup, updateConversation, type Conversation, type ConversationGroup,
} from "../api/learning";
import { useFocusTrap } from "../hooks/useFocusTrap";
import { Icon } from "./Icon";
import { SelectField } from "./SelectField";

type Action = { kind: "rename" | "move" | "delete"; conversation: Conversation }
  | { kind: "create-group" } | { kind: "rename-group" | "delete-group"; group: ConversationGroup };

export function ConversationSidebar({ conversations, currentId, loading, onNew, creating, onDeleted, hasMore, loadingMore, onLoadMore }: {
  conversations: Conversation[]; currentId?: string; loading: boolean; onNew: () => void;
  creating: boolean; onDeleted: (id: string) => void; hasMore: boolean; loadingMore: boolean; onLoadMore: () => void;
}) {
  const cache = useQueryClient();
  const groups = useQuery({ queryKey: ["conversation-groups"], queryFn: listConversationGroups });
  const [search, setSearch] = useState("");
  const [action, setAction] = useState<Action | null>(null);
  const [name, setName] = useState("");
  const [groupId, setGroupId] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const headingId = useId();
  const saving = useRef(false);
  const close = useCallback(() => { if (!saving.current) dialog.current?.close(); }, []);
  useFocusTrap(dialog, action !== null, close);
  useEffect(() => {
    if (action && !dialog.current?.open) {
      dialog.current?.showModal();
      if (window.innerWidth >= 760) dialog.current?.querySelector<HTMLInputElement>('input[name="name"]')?.focus();
    }
  }, [action]);
  const edit = useMutation({ mutationFn: async () => {
    if (!action) return;
    saving.current = true;
    switch (action.kind) {
      case "rename": return updateConversation(action.conversation.id, { title: name.trim() });
      case "move": return updateConversation(action.conversation.id, { group_id: groupId || null });
      case "delete": return deleteConversation(action.conversation.id);
      case "create-group": return createConversationGroup(name.trim());
      case "rename-group": return renameConversationGroup(action.group.id, name.trim());
      case "delete-group": return deleteConversationGroup(action.group.id);
    }
  }, onError: () => { saving.current = false; }, onSuccess: () => {
    saving.current = false;
    if (action?.kind === "delete") onDeleted(action.conversation.id);
    close();
    void cache.invalidateQueries({ queryKey: ["conversations"] });
    void cache.invalidateQueries({ queryKey: ["conversation"] });
    void cache.invalidateQueries({ queryKey: ["conversation-groups"] });
    void cache.invalidateQueries({ queryKey: ["learning-resume"] });
  } });
  const open = (next: Action) => {
    edit.reset(); setName("conversation" in next ? next.conversation.title : "group" in next ? next.group.name : "");
    setGroupId("conversation" in next ? next.conversation.group_id ?? "" : ""); setAction(next);
  };
  const openFromMenu = (event: React.MouseEvent<HTMLButtonElement>, next: Action) => {
    event.currentTarget.closest("details")?.removeAttribute("open");
    open(next);
  };
  const items = conversations.filter((conversation) => conversation.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const row = (conversation: Conversation) => <div className="chat-history-row" key={conversation.id}>
    <Link aria-current={conversation.id === currentId ? "page" : undefined} title={conversation.title} to={`/study/${conversation.id}`}>{conversation.title}</Link>
    <details className="chat-actions" onKeyDown={(event) => { if (event.key === "Escape") { event.currentTarget.open = false; event.currentTarget.querySelector("summary")?.focus(); } }}>
      <summary aria-label={`管理对话 ${conversation.title}`} title="管理对话">···</summary>
      <div><button type="button" onClick={(event) => openFromMenu(event, { kind: "rename", conversation })}>重命名</button><button type="button" onClick={(event) => openFromMenu(event, { kind: "move", conversation })}>移动到分组</button><button className="danger-text" type="button" onClick={(event) => openFromMenu(event, { kind: "delete", conversation })}>删除对话</button></div>
    </details>
  </div>;
  const title = action?.kind === "rename" ? "重命名对话" : action?.kind === "move" ? "移动对话" : action?.kind === "delete" ? "删除对话？" : action?.kind === "create-group" ? "新建分组" : action?.kind === "rename-group" ? "重命名分组" : "删除分组？";
  const deleting = action?.kind === "delete" || action?.kind === "delete-group";
  return <section className="chat-history" aria-labelledby="chat-history-title">
    <div className="sidebar-heading"><h2 id="chat-history-title">对话</h2><button className="icon-button" aria-label="新建分组" title="新建分组" onClick={() => open({ kind: "create-group" })} type="button"><Icon name="plus" /></button></div>
    <button className="button button--secondary chat-new" disabled={creating} onClick={onNew} type="button"><Icon name="plus" />{creating ? "创建中…" : "新建对话"}</button>
    <label className="visually-hidden" htmlFor="chat-search">搜索对话</label><input id="chat-search" name="chat-search" autoComplete="off" type="search" placeholder="搜索对话…" value={search} onChange={(event) => setSearch(event.target.value)} />
    {loading ? <p role="status">正在读取对话…</p> : null}
    <nav aria-label="历史对话">
      {groups.data?.map((group) => <details className="chat-group" key={group.id} open>
        <summary>{group.name}</summary>
        <div className="chat-group-tools"><button aria-label={`重命名分组 ${group.name}`} type="button" onClick={() => open({ kind: "rename-group", group })}>命名</button><button aria-label={`删除分组 ${group.name}`} type="button" onClick={() => open({ kind: "delete-group", group })}>删除分组</button></div>
        {items.filter((item) => item.group_id === group.id).map(row)}
      </details>)}
      <h3 className="chat-history-label">未分组</h3>
      {items.filter((item) => !item.group_id || !groups.data?.some((group) => group.id === item.group_id)).map(row)}
      {!loading && !items.length ? <p className="chat-history-empty">{search ? "没有匹配对话" : "暂无对话"}</p> : null}
    </nav>
    {hasMore ? <button className="button button--secondary" disabled={loadingMore} onClick={onLoadMore} type="button">{loadingMore ? "加载中…" : "更早的对话"}</button> : null}
    {groups.error ? <p role="alert">分组读取失败。<button className="button button--secondary" type="button" onClick={() => void groups.refetch()}>重试</button></p> : null}
    <dialog className="workspace-dialog conversation-dialog" aria-labelledby={headingId} ref={dialog} onClose={() => setAction(null)} onCancel={(event) => { if (edit.isPending) event.preventDefault(); }}>
      <div className="conversation-dialog__header"><h2 id={headingId}>{title}</h2><button aria-label="关闭" className="icon-button" disabled={edit.isPending} onClick={close} type="button"><Icon name="close" /></button></div>
      <form onSubmit={(event) => { event.preventDefault(); if (!edit.isPending) edit.mutate(); }}>
        {deleting ? <p>{action?.kind === "delete" ? `“${action.conversation.title}”及其消息将被清除，无法恢复；已生成的独立练习保留。` : "分组会被删除，对话保留并移到未分组。"}</p> : action?.kind === "move" ?
          <SelectField id={`${headingId}-group`} name="group_id" label="分组" value={groupId} onChange={setGroupId} options={[{ value: "", label: "未分组" }, ...(groups.data ?? []).map((group) => ({ value: group.id, label: group.name }))]} /> :
          <div className="conversation-dialog__field"><label htmlFor={`${headingId}-name`}>名称</label><input id={`${headingId}-name`} name="name" autoComplete="off" placeholder="例如：期末复习…" maxLength={action?.kind === "rename" ? 160 : 80} value={name} onChange={(event) => setName(event.target.value)} required /></div>}
        {edit.error ? <p role="alert">{edit.error.message}，可重试。</p> : null}
        <div className="conversation-dialog__actions"><button className="button button--quiet" disabled={edit.isPending} onClick={close} type="button">取消</button><button className={`button ${deleting ? "button--danger-quiet" : "button--primary"}`} disabled={edit.isPending || (!deleting && action?.kind !== "move" && !name.trim())} type="submit">{edit.isPending ? "保存中…" : deleting ? "确认删除" : "保存"}</button></div>
      </form>
    </dialog>
  </section>;
}
