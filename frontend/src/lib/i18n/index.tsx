"use client";

// Lightweight client-side i18n for the statically exported Control Plane UI.
// No server runtime is available (next.config `output: "export"`), so the
// locale lives in localStorage and is resolved on the client — mirroring how
// next-themes handles the color theme. Initial render is always DEFAULT_LOCALE
// to keep server and client markup identical; the stored locale is applied in
// an effect (same trade-off as the theme flash).

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { DEFAULT_LOCALE, messages, type Locale } from "./messages";
import { htmlLangOf, localeOf, nextLocale } from "./locale";
import { readServerLanguage, saveServerLanguage } from "./server-language";

const STORAGE_KEY = "agora.locale";

declare global {
  interface Window {
    // Set by the pre-paint bootstrap script in the root layout.
    __AGORA_LOCALE__?: Locale;
  }
}

type TranslateVars = Record<string, string | number>;

export type Translate = (key: string, vars?: TranslateVars) => string;
// Localizes a backend enum value (e.g. a run state). Falls back to the
// humanized raw value when the value is not mapped under `enums.<group>`.
export type TranslateEnum = (group: string, value: string) => string;

// Where the display language stands against the server's setting, which is the
// authority (CR-71): "unread" means the page could not ask, so it shows its cached
// choice and must not claim the backend writes in it; "saveFailed" means the button
// was pressed, the server refused or was unreachable, and the language did not change.
export type LanguageSync = "pending" | "synced" | "unread" | "saving" | "saveFailed";

type I18nContextValue = {
  locale: Locale;
  languageSync: LanguageSync;
  toggleLocale: () => void;
  t: Translate;
  te: TranslateEnum;
};

const I18nContext = createContext<I18nContextValue | null>(null);

function lookup(locale: Locale, key: string): string | undefined {
  let node: unknown = messages[locale];
  for (const part of key.split(".")) {
    if (node && typeof node === "object" && part in (node as Record<string, unknown>)) {
      node = (node as Record<string, unknown>)[part];
    } else {
      return undefined;
    }
  }
  return typeof node === "string" ? node : undefined;
}

function interpolate(template: string, vars?: TranslateVars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in vars ? String(vars[name]) : match,
  );
}

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(DEFAULT_LOCALE);
  const [languageSync, setLanguageSync] = useState<LanguageSync>("pending");

  useEffect(() => {
    // One-time hydration of the persisted (or browser-preferred) locale.
    // Initial render stays DEFAULT_LOCALE so server and client markup match; the
    // real locale is applied here. `data-i18n-ready` then reveals the tree (see
    // the guard style in layout), so the pre-hydration English HTML is never
    // shown flipping to Chinese — the swap happens while content is hidden.
    // No stored/bootstrapped preference → Chinese (this deployment's primary
    // audience); a stored choice is honored so the button sticks.
    const next: Locale = localeOf(window.__AGORA_LOCALE__ ?? window.localStorage.getItem(STORAGE_KEY));
    if (next !== DEFAULT_LOCALE) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- hydration-only sync from an external store; see comment above.
      setLocaleState(next);
    }
    document.documentElement.setAttribute("data-i18n-ready", "");
    // Then ask the authority. Its answer wins over the cache; the cache is updated so
    // the next page load paints in it straight away.
    let alive = true;
    readServerLanguage().then(
      (server) => {
        if (!alive) return;
        setLocaleState(server);
        try { window.localStorage.setItem(STORAGE_KEY, server); } catch { /* cache only */ }
        setLanguageSync("synced");
      },
      () => { if (alive) setLanguageSync("unread"); },
    );
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    document.documentElement.lang = htmlLangOf(locale);
  }, [locale]);

  // Saved first, switched second: the UI only changes language once the server has
  // it, so what the page shows and what the backend writes cannot silently diverge.
  const toggleLocale = useCallback(() => {
    // Not while the first read of the server's value is still out: its answer would
    // land after the save and overwrite the language just saved (codex, CR-71 s2).
    if (languageSync === "saving" || languageSync === "pending") return;
    const next = nextLocale(locale);
    setLanguageSync("saving");
    saveServerLanguage(next).then(
      (saved) => {
        setLocaleState(saved);
        try { window.localStorage.setItem(STORAGE_KEY, saved); } catch { /* cache only */ }
        setLanguageSync("synced");
      },
      () => setLanguageSync("saveFailed"),
    );
  }, [languageSync, locale]);

  const t = useCallback(
    (key: string, vars?: TranslateVars) => {
      const raw = lookup(locale, key) ?? lookup(DEFAULT_LOCALE, key) ?? key;
      return interpolate(raw, vars);
    },
    [locale],
  );

  const te = useCallback<TranslateEnum>(
    (group, value) => {
      const key = `enums.${group}.${value}`;
      const label = lookup(locale, key) ?? lookup(DEFAULT_LOCALE, key);
      return label ?? value.replaceAll("_", " ");
    },
    [locale],
  );

  const value = useMemo<I18nContextValue>(
    () => ({ locale, languageSync, toggleLocale, t, te }),
    [locale, languageSync, toggleLocale, t, te],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within a LocaleProvider");
  return ctx;
}
