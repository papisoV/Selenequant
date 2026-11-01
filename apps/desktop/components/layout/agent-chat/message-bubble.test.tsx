import { describe, it, expect } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageBubble } from "@/components/layout/agent-chat/message-bubble";

describe("MessageBubble 记忆透明行(透明性批2-9)", () => {
  it("memoryUsed > 0 时渲染'已参考 N 条历史教训'并默认收起", () => {
    render(
      <MessageBubble
        msg={{ id: "m1", role: "assistant", content: "结论是...", timestamp: 0, memoryUsed: 3 }}
      />
    );
    expect(screen.getByTestId("memory-provenance")).toBeInTheDocument();
    expect(screen.getByText(/已参考 3 条历史教训/)).toBeInTheDocument();
    expect(screen.queryByText(/按问题关键词检索/)).not.toBeInTheDocument();
  });

  it("点击展开说明注入机制", () => {
    render(
      <MessageBubble
        msg={{ id: "m1", role: "assistant", content: "结论是...", timestamp: 0, memoryUsed: 2 }}
      />
    );
    fireEvent.click(screen.getByText(/已参考 2 条历史教训/));
    expect(screen.getByText(/按问题关键词检索/)).toBeInTheDocument();
  });

  it("memoryUsed 为 0/缺省时不渲染透明行", () => {
    render(
      <MessageBubble msg={{ id: "m1", role: "assistant", content: "你好", timestamp: 0 }} />
    );
    expect(screen.queryByTestId("memory-provenance")).not.toBeInTheDocument();
  });

  it("用户消息永不渲染透明行(即使误带 memoryUsed)", () => {
    render(
      <MessageBubble
        msg={{ id: "m1", role: "user", content: "帮我看看", timestamp: 0, memoryUsed: 5 }}
      />
    );
    expect(screen.queryByTestId("memory-provenance")).not.toBeInTheDocument();
  });
});
