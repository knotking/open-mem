"use client";

/**
 * Light / dark, with a third state that matters: **system**.
 *
 * Defaulting to a fixed theme ignores an accessibility preference the operating
 * system already knows. The choice is persisted so it survives a reload, and
 * applied to the document element so CSS variables switch without a repaint
 * flash.
 */

import { useEffect, useState } from "react";

type Theme = "system" | "light" | "dark";

const LABEL: Record<Theme, string> = { system: "Auto", light: "Light", dark: "Dark" };
const NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    const stored = (localStorage.getItem("memdog-theme") as Theme | null) ?? "system";
    setTheme(stored);
    apply(stored);
  }, []);

  function apply(next: Theme) {
    const root = document.documentElement;
    if (next === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", next);
  }

  function cycle() {
    const next = NEXT[theme];
    setTheme(next);
    localStorage.setItem("memdog-theme", next);
    apply(next);
  }

  return (
    <button className="secondary theme" onClick={cycle} title={`Theme: ${LABEL[theme]}`}>
      {theme === "dark" ? "◑" : theme === "light" ? "◐" : "◒"} {LABEL[theme]}
    </button>
  );
}
