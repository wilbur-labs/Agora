// The display language is the server's setting (CR-71 slice 2): the backend writes
// run-log notes and attention items in it, so the UI and those records only agree if
// there is one authority. localStorage is just the pre-paint cache of that answer.

import { getApiBase } from "@/lib/api";

import { LOCALES, type Locale } from "./locale";

function languageFrom(payload: unknown): Locale {
  const value = (payload as { language?: unknown } | null)?.language;
  if (!LOCALES.includes(value as Locale)) {
    // An answer we cannot read is not "Chinese": the caller treats it as a failed read.
    throw new Error(`unexpected display language: ${String(value)}`);
  }
  return value as Locale;
}

export async function readServerLanguage(): Promise<Locale> {
  const response = await fetch(`${getApiBase()}/api/settings/display-language`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return languageFrom(await response.json());
}

export async function saveServerLanguage(language: Locale): Promise<Locale> {
  const response = await fetch(`${getApiBase()}/api/settings/display-language`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ language }),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return languageFrom(await response.json());
}
