/** 主题工具：与 index.html 内联脚本共用同一 localStorage 键，保证首帧无闪烁。 */

const KEY = "tcms-theme";
export type Theme = "dark" | "light";

export function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "light" ? "light" : "dark";
}

export function applyTheme(t: Theme): void {
  const el = document.documentElement;
  el.classList.remove("theme-dark", "theme-light");
  el.classList.add(`theme-${t}`);
  el.dataset.theme = t;
  try {
    localStorage.setItem(KEY, t);
  } catch {
    /* 隐私模式等场景忽略 */
  }
}

export function toggleTheme(): Theme {
  const next: Theme = currentTheme() === "dark" ? "light" : "dark";
  applyTheme(next);
  return next;
}
