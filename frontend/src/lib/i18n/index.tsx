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

type I18nContextValue = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
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

  useEffect(() => {
    // One-time hydration of the persisted (or browser-preferred) locale.
    // Initial render stays DEFAULT_LOCALE so server and client markup match; the
    // real locale is applied here. `data-i18n-ready` then reveals the tree (see
    // the guard style in layout), so the pre-hydration English HTML is never
    // shown flipping to Chinese — the swap happens while content is hidden.
    const bootstrapped = window.__AGORA_LOCALE__;
    const stored = window.localStorage.getItem(STORAGE_KEY);
    const next: Locale | null =
      bootstrapped === "en" || bootstrapped === "zh"
        ? bootstrapped
        : stored === "en" || stored === "zh"
          ? stored
          : window.navigator.language.toLowerCase().startsWith("zh")
            ? "zh"
            : null;
    if (next && next !== DEFAULT_LOCALE) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- hydration-only sync from an external store; see comment above.
      setLocaleState(next);
    }
    document.documentElement.setAttribute("data-i18n-ready", "");
  }, []);

  useEffect(() => {
    document.documentElement.lang = locale === "zh" ? "zh-Hans" : "en";
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    window.localStorage.setItem(STORAGE_KEY, next);
  }, []);

  const toggleLocale = useCallback(() => {
    setLocaleState((current) => {
      const next: Locale = current === "zh" ? "en" : "zh";
      window.localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

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
    () => ({ locale, setLocale, toggleLocale, t, te }),
    [locale, setLocale, toggleLocale, t, te],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within a LocaleProvider");
  return ctx;
}
