import { Link } from "react-router-dom";

import { Icon } from "./Icon";
import { ThemeToggle } from "./ThemeToggle";

interface AppHeaderProps {
  onMenu?: () => void;
  children?: React.ReactNode;
}

export function AppHeader({ onMenu, children }: AppHeaderProps) {
  return (
    <header className="app-header">
      <div className="app-header__leading">
        {onMenu ? (
          <button aria-label="打开资料导航" className="icon-button mobile-only" onClick={onMenu} type="button">
            <Icon name="menu" />
          </button>
        ) : null}
        <Link aria-label="Review Agent 资料库" className="brand" to="/">
          <span className="brand__mark"><Icon name="book" /></span>
          <span>Review Agent</span>
        </Link>
      </div>
      <div className="app-header__actions">
        {children}
        <ThemeToggle />
      </div>
    </header>
  );
}
