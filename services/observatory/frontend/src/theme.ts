import { createContext, useContext } from "react";

export interface ThemeDefinition {
  name: string;
  description: string;
  colors: {
    bg: string;
    bgOff: string;
    bgDark: string;
    border: string;
    borderStrong: string;
    text: string;
    textMid: string;
    textMuted: string;
    textFaint: string;
    green: string;
    navBg: string;
    navText: string;
    navTextMuted: string;
    navBorder: string;
  };
  font: string;
}

export const THEMES: Record<string, ThemeDefinition> = {
  newsprint: {
    name: "Newsprint",
    description: "Monospace terminal. Neutral, engineered feel.",
    colors: {
      bg: "#ffffff",
      bgOff: "#f7f7f7",
      bgDark: "#eeeeee",
      border: "#cccccc",
      borderStrong: "#333333",
      text: "#111111",
      textMid: "#444444",
      textMuted: "#888888",
      textFaint: "#bbbbbb",
      green: "#1a7a1a",
      navBg: "#ffffff",
      navText: "#111111",
      navTextMuted: "#888888",
      navBorder: "#cccccc",
    },
    font: "'Courier New', Courier, monospace",
  },
  ft: {
    name: "FT",
    description: "Salmon + Georgia serif. Warm, journalistic authority.",
    colors: {
      bg: "#FFF1E0",
      bgOff: "#F2E3CE",
      bgDark: "#EDD9BC",
      border: "#CCC0AB",
      borderStrong: "#1A1919",
      text: "#1A1919",
      textMid: "#4A3728",
      textMuted: "#9D8574",
      textFaint: "#C4A98C",
      green: "#006A4E",
      navBg: "#1A1919",
      navText: "#FFF1E0",
      navTextMuted: "rgba(255,255,255,0.6)",
      navBorder: "#333333",
    },
    font: "Georgia, 'Times New Roman', serif",
  },
  gs: {
    name: "GS",
    description: "Navy + Helvetica. Clean, institutional precision.",
    colors: {
      bg: "#FFFFFF",
      bgOff: "#F5F6F7",
      bgDark: "#E8EAEC",
      border: "#D0D3D9",
      borderStrong: "#0B2D6E",
      text: "#0B2D6E",
      textMid: "#2B4A8A",
      textMuted: "#6B7BA0",
      textFaint: "#A0AABF",
      green: "#007A5E",
      navBg: "#0B2D6E",
      navText: "#ffffff",
      navTextMuted: "rgba(255,255,255,0.6)",
      navBorder: "#0B2D6E",
    },
    font: "'Helvetica Neue', Helvetica, Arial, sans-serif",
  },
};

const STORAGE_KEY = "ate-theme";

export function getStoredTheme(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && stored in THEMES) return stored;
  } catch {
    // localStorage unavailable
  }
  return "newsprint";
}

export function applyTheme(themeKey: string) {
  const theme = THEMES[themeKey];
  if (!theme) return;

  const root = document.documentElement;

  root.style.setProperty("--color-bg", theme.colors.bg);
  root.style.setProperty("--color-bg-off", theme.colors.bgOff);
  root.style.setProperty("--color-bg-dark", theme.colors.bgDark);
  root.style.setProperty("--color-border", theme.colors.border);
  root.style.setProperty("--color-border-strong", theme.colors.borderStrong);
  root.style.setProperty("--color-text", theme.colors.text);
  root.style.setProperty("--color-text-mid", theme.colors.textMid);
  root.style.setProperty("--color-text-muted", theme.colors.textMuted);
  root.style.setProperty("--color-text-faint", theme.colors.textFaint);
  root.style.setProperty("--color-green", theme.colors.green);
  root.style.setProperty("--color-nav-bg", theme.colors.navBg);
  root.style.setProperty("--color-nav-text", theme.colors.navText);
  root.style.setProperty("--color-nav-text-muted", theme.colors.navTextMuted);
  root.style.setProperty("--color-nav-border", theme.colors.navBorder);
  root.style.setProperty("--font-mono", theme.font);

  try {
    localStorage.setItem(STORAGE_KEY, themeKey);
  } catch {
    // localStorage unavailable
  }
}

export const ThemeContext = createContext<{
  current: string;
  setTheme: (key: string) => void;
}>({ current: "newsprint", setTheme: () => {} });

export function useTheme() {
  return useContext(ThemeContext);
}
