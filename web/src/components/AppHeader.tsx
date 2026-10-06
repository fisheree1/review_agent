import { useEffect, useCallback, useId, useRef, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

import { useFocusTrap } from "../hooks/useFocusTrap";
import { BrandMark } from "./BrandMark";
import { Icon } from "./Icon";
import { ThemeToggle } from "./ThemeToggle";

interface AppHeaderProps {
  onMenu?: () => void;
  children?: React.ReactNode;
}

const destinations = [
  { path: "/", label: "资料库", icon: "library" },
  { path: "/study", label: "学习空间", icon: "chat" },
  { path: "/quizzes", label: "练习", icon: "book" },
  { path: "/review", label: "复习", icon: "review" },
  { path: "/account", label: "账号", icon: "user" },
] as const;

export function AppHeader({ onMenu, children }: AppHeaderProps) {
  const [collapsed, setCollapsed] = useState(() => document.documentElement.dataset.sidebarCollapsed === "true");
  useEffect(() => {
    document.documentElement.dataset.sidebarCollapsed = String(collapsed);
    try { localStorage.setItem("review-agent-sidebar-v1", String(collapsed)); } catch { /* Session preference still works. */ }
  }, [collapsed]);
  const reading = useLocation().pathname.startsWith("/documents/");
  const menu = useRef<HTMLDialogElement>(null);
  const menuId = useId();
  const [menuOpen, setMenuOpen] = useState(false);
  const closeMenu = useCallback(() => menu.current?.close(), []);
  useFocusTrap(menu, menuOpen, closeMenu);
  const links = (close?: () => void) => destinations.map(({ path, label, icon }) => (
    <NavLink aria-label={label} title={label} end={path === "/"} key={path} onClick={close} to={path}>
      <Icon name={icon} /><span>{label}</span>
    </NavLink>
  ));
  return <>
    <header className={`app-header${reading ? "" : " app-sidebar"}`}>
      <div className="app-header__leading">
        {onMenu ? <button aria-label="打开资料导航" className="icon-button mobile-only" onClick={onMenu} type="button"><Icon name="menu" /></button> : null}
        <div className="app-header__identity">
          <Link aria-label="Review Agent 资料库" className="brand" translate="no" to="/">
            <span className="brand__mark"><BrandMark /></span><span>Review Agent</span>
          </Link>
          {!reading ? <button aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"} aria-expanded={!collapsed} aria-controls={`${menuId}-desktop`} className="icon-button sidebar-toggle" onClick={() => setCollapsed((value) => !value)} type="button" title={collapsed ? "展开侧边栏" : "收起侧边栏"}><Icon name="panel" /></button> : null}
        </div>
        {!reading ? <p className="navigation-caption">个人学习工作台</p> : null}
        <nav aria-label="主要导航" id={`${menuId}-desktop`} className="primary-nav">{links()}</nav>
        <button aria-controls={menuId} aria-expanded={menuOpen} aria-label="打开主导航" className="icon-button app-header__menu" onClick={() => { menu.current?.showModal(); setMenuOpen(true); }} type="button"><Icon name="menu" /></button>
      </div>
      <div className="app-header__actions">
        {!reading ? <span className="workspace-privacy"><Icon name="shield" /><span>私有空间</span></span> : null}
        {children}<ThemeToggle />
      </div>
    </header>
    <dialog aria-labelledby={`${menuId}-title`} className="navigation-dialog" id={menuId} onClose={() => setMenuOpen(false)} ref={menu}>
      <div className="navigation-dialog__heading"><h2 id={`${menuId}-title`}>学习导航</h2>
        <button aria-label="关闭主导航" className="icon-button" onClick={() => menu.current?.close()} type="button"><Icon name="close" /></button>
      </div>
      <nav aria-label="主要导航">{links(() => menu.current?.close())}</nav>
    </dialog>
  </>;
}
