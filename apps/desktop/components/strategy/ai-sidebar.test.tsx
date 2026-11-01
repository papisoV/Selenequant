import { describe, it, expect } from "vitest";
import {
  deltaTextFromData,
  extractYamlBlock,
  extractBacktestId,
  normalizeQuickReplyId,
  extractStructuredQuickReplies,
  extractQuickReplies,
  cleanReasoningText,
  formatReasoningSummary,
  isReasoningEvent,
  reasoningStepFromEvent,
  mapApiMessages,
} from "./ai-sidebar";

// 行为锁: 断言 ai-sidebar 纯函数的可观察契约(输入→输出)。
// cleanroom 重写必须保持这些行为; 撤掉实现改动 → 本文件必红。

describe("deltaTextFromData", () => {
  it("字符串直通", () => expect(deltaTextFromData("hi")).toBe("hi"));
  it("对象取 .text", () => expect(deltaTextFromData({ text: "x" })).toBe("x"));
  it("其它 → 空串", () => {
    expect(deltaTextFromData(null)).toBe("");
    expect(deltaTextFromData({ foo: 1 })).toBe("");
    expect(deltaTextFromData({ text: 42 })).toBe("");
  });
});

describe("extractYamlBlock", () => {
  it("提取 ```yaml 代码块并 trim", () => {
    expect(extractYamlBlock("前置\n```yaml\nname: a\n```\n后置")).toBe("name: a");
  });
  it("大小写不敏感", () => {
    expect(extractYamlBlock("```YAML\nk: v\n```")).toBe("k: v");
  });
  it("无块 → null", () => expect(extractYamlBlock("no block")).toBeNull());
});

describe("extractBacktestId", () => {
  it("优先匹配 'Backtest Complete — ID: bt-…'", () => {
    expect(extractBacktestId("Backtest Complete — ID: bt-deadbeef")).toBe("bt-deadbeef");
  });
  it("破折号变体 / 大小写", () => {
    expect(extractBacktestId("backtest complete - id: bt-abc123")).toBe("bt-abc123");
  });
  it("回落抓裸 bt-hex", () => {
    expect(extractBacktestId("结果 bt-123456 见附件")).toBe("bt-123456");
  });
  it("无 → null", () => expect(extractBacktestId("无 id")).toBeNull());
});

describe("normalizeQuickReplyId", () => {
  it("有值用 trim 后的值", () => expect(normalizeQuickReplyId("  A ", 0)).toBe("A"));
  it("空值回落 index+1 字符串", () => expect(normalizeQuickReplyId("", 0)).toBe("1"));
});

describe("extractStructuredQuickReplies", () => {
  it("接受 quick_replies 键", () => {
    const out = extractStructuredQuickReplies({ quick_replies: ["甲", "乙"] });
    expect(out.map((o) => o.label)).toEqual(["甲", "乙"]);
    expect(out[0].id).toBe("1");
  });
  it("接受 camelCase / options 别名", () => {
    expect(extractStructuredQuickReplies({ quickReplies: ["x"] })).toHaveLength(1);
    expect(extractStructuredQuickReplies({ options: ["y"] })).toHaveLength(1);
  });
  it("对象项: label/value/id/description 别名解析", () => {
    const out = extractStructuredQuickReplies({
      quick_replies: [{ title: "标题", message: "内容", key: "k1", description: "d" }],
    });
    expect(out[0]).toMatchObject({ id: "k1", label: "标题", value: "内容", description: "d" });
  });
  it("无 value 时回落用 label(不会被剔除)", () => {
    const out = extractStructuredQuickReplies({ quick_replies: [{ title: "only" }, {}] });
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ label: "only", value: "only" });
  });
  it("上限 6 条", () => {
    const many = Array.from({ length: 10 }, (_, i) => `o${i}`);
    expect(extractStructuredQuickReplies({ quick_replies: many })).toHaveLength(6);
  });
  it("非数组/非对象 → 空", () => {
    expect(extractStructuredQuickReplies(null)).toEqual([]);
    expect(extractStructuredQuickReplies({ quick_replies: "x" })).toEqual([]);
  });
});

describe("extractQuickReplies", () => {
  it("触发段后抓编号项, 去 ** 加粗", () => {
    const { quickReplies, cleanedContent } = extractQuickReplies(
      "正文\n你可以直接回复数字即可\n1. **选项一**\n2. 选项二\n"
    );
    expect(quickReplies.map((q) => q.label)).toEqual(["选项一", "选项二"]);
    expect(quickReplies[0].id).toBe("1");
    // 触发句保留在正文, 选项行被摘走
    expect(cleanedContent).toContain("你可以直接回复数字即可");
    expect(cleanedContent).not.toContain("选项一");
  });
  it("未触发时不动正文", () => {
    const { quickReplies, cleanedContent } = extractQuickReplies("普通文本\n1. 不该抓");
    expect(quickReplies).toEqual([]);
    expect(cleanedContent).toBe("普通文本\n1. 不该抓");
  });
  it("触发段内项目符号也可抓", () => {
    const { quickReplies } = extractQuickReplies("你回复数字即可\n- 甲\n- 乙");
    expect(quickReplies.map((q) => q.label)).toEqual(["甲", "乙"]);
  });
  it("上限 6 条", () => {
    const body = "你回复数字即可\n" + Array.from({ length: 9 }, (_, i) => `${i + 1}. o`).join("\n");
    expect(extractQuickReplies(body).quickReplies).toHaveLength(6);
  });
});

describe("cleanReasoningText", () => {
  it("去 <reasoning> 标签(大小写不敏感)", () => {
    expect(cleanReasoningText("<reasoning>hi</REASONING>")).toBe("hi");
  });
  it("去行首 Step/Round N: Think/… 前缀", () => {
    expect(cleanReasoningText("Step 1: Think about it")).toBe("about it");
    // 正则前缀吃掉 "Round 2 Reflect" 后残留 ": " → trim 后为 ": done"（记录现状）
    expect(cleanReasoningText("Round 2 Reflect: done")).toBe(": done");
  });
  it("trim 空白", () => expect(cleanReasoningText("  x  ")).toBe("x"));
});

describe("formatReasoningSummary", () => {
  it("thinking → 清洗 content", () => {
    expect(
      formatReasoningSummary({ type: "thinking", data: { content: "<reasoning>想法</reasoning>" } } as never)
    ).toBe("想法");
  });
  it("tool_call → tool(json(args))", () => {
    expect(
      formatReasoningSummary({ type: "tool_call", data: { tool: "run", arguments: { a: 1 } } } as never)
    ).toBe('run({"a":1})');
  });
  it("tool_result 超 120 字截断加省略号", () => {
    const long = "z".repeat(200);
    const out = formatReasoningSummary({ type: "tool_result", data: { output: long } } as never);
    expect(out).toBe("z".repeat(120) + "…");
  });
});

describe("isReasoningEvent", () => {
  it("四类推理事件 → true", () => {
    for (const t of ["thinking", "tool_call", "tool_result", "reflection"]) {
      expect(isReasoningEvent(t)).toBe(true);
    }
  });
  it("其它(heartbeat/delta/token) → false", () => {
    for (const t of ["heartbeat", "delta", "token", "done"]) {
      expect(isReasoningEvent(t)).toBe(false);
    }
  });
});

describe("reasoningStepFromEvent", () => {
  it("对象 data 原样透传", () => {
    expect(reasoningStepFromEvent({ type: "tool_call", data: { tool: "x" } })).toEqual({
      type: "tool_call",
      data: { tool: "x" },
    });
  });
  it("标量 data → { content: String(...) }", () => {
    expect(reasoningStepFromEvent({ type: "thinking", data: "hi" })).toEqual({
      type: "thinking",
      data: { content: "hi" },
    });
  });
  it("null data → { content: '' }", () => {
    expect(reasoningStepFromEvent({ type: "reflection", data: null })).toEqual({
      type: "reflection",
      data: { content: "" },
    });
  });
});

describe("mapApiMessages", () => {
  it("映射 id/role/content", () => {
    expect(mapApiMessages([{ id: "1", role: "user", content: "hi" }])).toEqual([
      { id: "1", role: "user", content: "hi", reasoningSteps: undefined },
    ]);
  });
  it("extra 为数组 → reasoningSteps", () => {
    const out = mapApiMessages([{ id: "1", role: "assistant", content: "x", extra: [{ type: "thinking" }] }]);
    expect(Array.isArray(out[0].reasoningSteps)).toBe(true);
  });
  it("extra 非数组 → undefined", () => {
    expect(mapApiMessages([{ id: "1", role: "user", content: "x", extra: {} }])[0].reasoningSteps).toBeUndefined();
  });
});
