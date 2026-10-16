const CONDITION_VALUE_OPTIONS = Object.freeze({
  rice_type: Object.freeze([
    Object.freeze({ value: "indica", label: "籼型" }),
    Object.freeze({ value: "japonica", label: "粳型" }),
    Object.freeze({ value: "mixed", label: "籼粳混合" }),
  ]),
  variety_kind: Object.freeze([
    Object.freeze({ value: "inbred", label: "常规稻" }),
    Object.freeze({ value: "hybrid", label: "杂交稻" }),
    Object.freeze({ value: "sterile", label: "不育系" }),
  ]),
});

const TECHNICAL_TEXT_REPLACEMENTS = Object.freeze([
  ["rice_type=indica", "籼粳类型=籼型"],
  ["rice_type=japonica", "籼粳类型=粳型"],
  ["rice_type=mixed", "籼粳类型=籼粳混合"],
  ["variety_kind=inbred", "品种类型=常规稻"],
  ["variety_kind=hybrid", "品种类型=杂交稻"],
  ["variety_kind=sterile", "品种类型=不育系"],
]);

export function conditionValueOptions(field) {
  return CONDITION_VALUE_OPTIONS[field] || [];
}

export function conditionValueLabel(field, value) {
  return conditionValueOptions(field).find((option) => option.value === value)?.label ?? String(value ?? "");
}

export function localizeConditionText(value) {
  return TECHNICAL_TEXT_REPLACEMENTS.reduce(
    (text, [technical, chinese]) => text.replaceAll(technical, chinese),
    String(value ?? ""),
  );
}

