// Locale identity and the rules that pick one. No imports, so the layout's
// pre-paint script, the provider and the tests all agree on one definition.

export type Locale = "en" | "zh" | "ja";

export const LOCALES: readonly Locale[] = ["zh", "ja", "en"] as const;

// What an unknown or missing stored choice resolves to: this deployment's
// primary reader reads Chinese first.
export const PREFERRED_LOCALE: Locale = "zh";

export function localeOf(value: unknown): Locale {
  return LOCALES.includes(value as Locale) ? (value as Locale) : PREFERRED_LOCALE;
}

// The language button cycles 中文 → 日本語 → English → 中文.
export function nextLocale(current: Locale): Locale {
  return LOCALES[(LOCALES.indexOf(current) + 1) % LOCALES.length];
}

export function htmlLangOf(locale: Locale): string {
  return locale === "zh" ? "zh-Hans" : locale;
}

// BCP 47 tag for Intl date/number formatting.
export function intlLocaleOf(locale: Locale): string {
  return locale === "zh" ? "zh-CN" : locale === "ja" ? "ja-JP" : "en-US";
}
