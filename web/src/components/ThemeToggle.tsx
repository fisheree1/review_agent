import { useEffect, useState } from "react";

import { Icon } from "./Icon";

type Theme = "light" | "dark";

function initialTheme(): Theme {
  const stored = localStorage.getItem("review-agent-theme");
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("review-agent-theme", theme);
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
