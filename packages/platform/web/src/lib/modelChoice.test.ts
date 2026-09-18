import { describe, expect, it } from "vitest";
import { choiceLabel, choiceToParams, isOverridden, parseModelChoice } from "./modelChoice";

describe("modelChoice", () => {
  it("坏数据/空值一律回落「跟随设置」，不让页面崩", () => {
    for (const raw of [null, "", "{", "[]", '{"mode":"pick"}', '{"mode":"pick","id":"  "}', '"x"']) {
      expect(parseModelChoice(raw)).toEqual({ mode: "follow" });
    }
  });

  it("手动指定的模型被原样保留（含 base_url）", () => {
    const c = parseModelChoice('{"mode":"pick","id":"qwen-turbo","base_url":"https://x/v1"}');
    expect(c).toEqual({ mode: "pick", id: "qwen-turbo", base_url: "https://x/v1" });
    expect(choiceToParams(c)).toEqual({ model: "qwen-turbo", base_url: "https://x/v1" });
    expect(choiceLabel(c, "跟随设置")).toBe("qwen-turbo");
    expect(isOverridden(c)).toBe(true);
  });

  it("跟随设置 → 不下发任何模型参数（交给后端统一解析链）", () => {
    const c = parseModelChoice(null);
    expect(choiceToParams(c)).toEqual({});
    expect(choiceLabel(c, "跟随设置（deepseek-v4-pro-0813）")).toContain("跟随设置");
    expect(isOverridden(c)).toBe(false);
  });

  it("只有模型没有 base_url 时，不塞空的 base_url 字段", () => {
    expect(choiceToParams({ mode: "pick", id: "m" })).toEqual({ model: "m" });
  });
});
