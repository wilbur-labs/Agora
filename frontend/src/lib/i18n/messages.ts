// Aggregates every namespace dictionary into one message tree per locale.
// Pure data (no React) so it can be imported by tests and the provider alike.
// Add a namespace by importing its dictionary and registering it below.

import { common } from "./dictionaries/common";
import { home } from "./dictionaries/home";
import { controlPlane } from "./dictionaries/control-plane";
import { enums } from "./dictionaries/enums";
import { portfolio } from "./dictionaries/portfolio";
import { requirements } from "./dictionaries/requirements";
import { runs } from "./dictionaries/runs";
import { workflows } from "./dictionaries/workflows";
import { attention } from "./dictionaries/attention";
import { settings } from "./dictionaries/settings";

export type Locale = "en" | "zh";

export const LOCALES: readonly Locale[] = ["en", "zh"] as const;
export const DEFAULT_LOCALE: Locale = "en";

export const messages = {
  en: {
    common: common.en,
    home: home.en,
    controlPlane: controlPlane.en,
    enums: enums.en,
    portfolio: portfolio.en,
    requirements: requirements.en,
    runs: runs.en,
    workflows: workflows.en,
    attention: attention.en,
    settings: settings.en,
  },
  zh: {
    common: common.zh,
    home: home.zh,
    controlPlane: controlPlane.zh,
    enums: enums.zh,
    portfolio: portfolio.zh,
    requirements: requirements.zh,
    runs: runs.zh,
    workflows: workflows.zh,
    attention: attention.zh,
    settings: settings.zh,
  },
} as const;
