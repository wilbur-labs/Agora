import assert from "node:assert/strict";
import { test } from "node:test";

import { htmlLangOf, intlLocaleOf, localeOf, nextLocale } from "../src/lib/i18n/locale.ts";

test("a stored choice is honored and anything else is Chinese", () => {
  assert.equal(localeOf("ja"), "ja");
  assert.equal(localeOf("en"), "en");
  assert.equal(localeOf("zh"), "zh");
  assert.equal(localeOf(null), "zh");
  assert.equal(localeOf("fr"), "zh");
});

test("the language button cycles through all three and comes back", () => {
  assert.equal(nextLocale("zh"), "ja");
  assert.equal(nextLocale("ja"), "en");
  assert.equal(nextLocale("en"), "zh");
});

test("each locale has its own html lang and Intl tag", () => {
  assert.equal(htmlLangOf("zh"), "zh-Hans");
  assert.equal(htmlLangOf("ja"), "ja");
  assert.equal(intlLocaleOf("ja"), "ja-JP");
  assert.equal(intlLocaleOf("en"), "en-US");
});
