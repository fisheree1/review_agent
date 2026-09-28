import { useEffect, useState } from "react";

import { Icon } from "./Icon";
import { applyTheme, initialTheme, type Theme } from "../theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    applyTheme(theme);
    try { localStorage.setItem("review-agent-theme", theme); }
    catch { /* Keep the selected theme for this page when storage is unavailable. */ }
  }, [theme]);

  const nextTheme = theme === "light" ? "dark" : "light";
  return (
    <button
      aria-label={`切换到${nextTheme === "dark" ? "深色" : "浅色"}模式`}
      className="icon-button"
      onClick={() => setTheme(nextTheme)}
      title={`切换到${nextTheme === "dark" ? "深色" : "浅色"}模式`}
      type="button"
    >
      <Icon name={theme === "light" ? "moon" : "sun"} />
    </button>
  );
}
