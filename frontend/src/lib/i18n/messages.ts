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
import { workspace } from "./dictionaries/workspace";
import { conversation } from "./dictionaries/conversation";
import { today } from "./dictionaries/today";
import { projectChat } from "./dictionaries/project-chat";
import { environments, skills } from "./dictionaries/environments";
import { work } from "./dictionaries/work";

import type { Locale } from "./locale";

export type { Locale } from "./locale";
export { LOCALES } from "./locale";
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
    workspace: workspace.en,
    conversation: conversation.en,
    today: today.en,
    projectChat: projectChat.en,
    environments: environments.en,
    skills: skills.en,
    work: work.en,
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
    workspace: workspace.zh,
    conversation: conversation.zh,
    today: today.zh,
    projectChat: projectChat.zh,
    environments: environments.zh,
    skills: skills.zh,
    work: work.zh,
  },
  ja: {
    common: common.ja,
    home: home.ja,
    controlPlane: controlPlane.ja,
    enums: enums.ja,
    portfolio: portfolio.ja,
    requirements: requirements.ja,
    runs: runs.ja,
    workflows: workflows.ja,
    attention: attention.ja,
    settings: settings.ja,
    workspace: workspace.ja,
    conversation: conversation.ja,
    today: today.ja,
    projectChat: projectChat.ja,
    environments: environments.ja,
    skills: skills.ja,
    work: work.ja,
  },
} as const;
