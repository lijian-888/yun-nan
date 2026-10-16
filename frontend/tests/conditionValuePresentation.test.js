import test from "node:test";
import assert from "node:assert/strict";

import {
  conditionValueLabel,
  conditionValueOptions,
  localizeConditionText,
} from "../src/conditionValuePresentation.js";

test("rice type and variety kind are presented as Chinese choices while retaining database codes", () => {
  assert.deepEqual(conditionValueOptions("rice_type"), [
    { value: "indica", label: "籼型" },
    { value: "japonica", label: "粳型" },
    { value: "mixed", label: "籼粳混合" },
  ]);
  assert.deepEqual(conditionValueOptions("variety_kind"), [
    { value: "inbred", label: "常规稻" },
    { value: "hybrid", label: "杂交稻" },
    { value: "sterile", label: "不育系" },
  ]);
  assert.equal(conditionValueLabel("rice_type", "indica"), "籼型");
  assert.equal(conditionValueLabel("variety_kind", "inbred"), "常规稻");
});

test("model explanations do not expose database field names or English codes", () => {
  const localized = localizeConditionText(
    "将‘籼型常规稻’解释为 rice_type=indica 且 variety_kind=inbred。",
  );

  assert.equal(localized, "将‘籼型常规稻’解释为 籼粳类型=籼型 且 品种类型=常规稻。");
  assert.doesNotMatch(localized, /rice_type|variety_kind|indica|inbred/);
});

test("unknown free text remains unchanged", () => {
  assert.equal(localizeConditionText("优先考虑云南审定品种。"), "优先考虑云南审定品种。");
});
