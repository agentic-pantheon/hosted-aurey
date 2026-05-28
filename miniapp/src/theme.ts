/** Sync light/dark class with Telegram (or system) and match WebApp chrome to theme background. */

export function applyAppTheme(): void {
  const wa = window.Telegram?.WebApp;
  const root = document.documentElement;
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const scheme = wa?.colorScheme ?? (prefersDark ? "dark" : "light");
  root.classList.toggle("dark", scheme === "dark");
  if (wa?.setHeaderColor) {
    wa.setHeaderColor(scheme === "dark" ? "#171717" : "#ffffff");
  }
  if (wa?.setBackgroundColor) {
    wa.setBackgroundColor(scheme === "dark" ? "#171717" : "#ffffff");
  }
}
