import assert from "node:assert/strict";
import { test } from "node:test";

import { attention } from "../src/lib/i18n/dictionaries/attention.ts";
import { common } from "../src/lib/i18n/dictionaries/common.ts";
import { controlPlane } from "../src/lib/i18n/dictionaries/control-plane.ts";
import { conversation } from "../src/lib/i18n/dictionaries/conversation.ts";
import { enums } from "../src/lib/i18n/dictionaries/enums.ts";
import { environments, skills } from "../src/lib/i18n/dictionaries/environments.ts";
import { home } from "../src/lib/i18n/dictionaries/home.ts";
import { portfolio } from "../src/lib/i18n/dictionaries/portfolio.ts";
import { projectChat } from "../src/lib/i18n/dictionaries/project-chat.ts";
import { requirements } from "../src/lib/i18n/dictionaries/requirements.ts";
import { runs } from "../src/lib/i18n/dictionaries/runs.ts";
import { settings } from "../src/lib/i18n/dictionaries/settings.ts";
import { today } from "../src/lib/i18n/dictionaries/today.ts";
import { workflows } from "../src/lib/i18n/dictionaries/workflows.ts";
import { work } from "../src/lib/i18n/dictionaries/work.ts";
import { workspace } from "../src/lib/i18n/dictionaries/workspace.ts";

// A key missing from a locale silently falls back to English at runtime, which
// is exactly the text the person reading this UI may not be able to read. So
// every locale must carry every English key, with the same placeholders.

const dictionaries: Record<string, Record<string, unknown>> = {
  attention, common, controlPlane, conversation, enums, environments, skills, home,
  portfolio, projectChat, requirements, runs, settings, today, work, workflows, workspace,
};

const OTHER_LOCALES = ["zh", "ja"] as const;

function leaves(node: unknown, prefix = ""): Map<string, string> {
  const out = new Map<string, string>();
  if (typeof node === "string") {
    out.set(prefix, node);
    return out;
  }
  if (node && typeof node === "object") {
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      for (const [k, v] of leaves(value, prefix ? `${prefix}.${key}` : key)) out.set(k, v);
    }
  }
  return out;
}

function placeholders(text: string): string[] {
  return [...text.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
}

function backslashes(text: string): number {
  return [...text].filter((c) => c === String.fromCharCode(92)).length;
}

for (const [name, dict] of Object.entries(dictionaries)) {
  for (const locale of OTHER_LOCALES) {
    test(`${name}: ${locale} has every English key with the same placeholders`, () => {
      assert.ok(dict[locale], `${name} has no ${locale} block`);
      const en = leaves(dict.en);
      const other = leaves(dict[locale]);
      const missing = [...en.keys()].filter((k) => !other.has(k));
      const extra = [...other.keys()].filter((k) => !en.has(k));
      assert.deepEqual(missing, [], `${name}.${locale} is missing keys`);
      assert.deepEqual(extra, [], `${name}.${locale} has keys English does not`);
      for (const [key, text] of en) {
        const translated = other.get(key)!;
        assert.deepEqual(placeholders(translated), placeholders(text), `${name}.${locale}.${key} placeholders differ`);
        assert.equal(backslashes(translated), backslashes(text), `${name}.${locale}.${key} backslash count differs`);
      }
    });
  }
}
