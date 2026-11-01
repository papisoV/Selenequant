import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TodayView } from "@/components/research/today-view";
import type { AgentActivityResponse, TodayResponse } from "@/components/research/today-types";

/**
 * SELENE IS WORKING 段的实时化(B2):running 调查会优先于 mission phase;
 * 最近动作流只取 5 条;聚合端点缺失时退回 mission 读数而不是编造工作态。
 */

function today(overrides: Partial<TodayResponse> = {}): TodayResponse {
  return {
    last_handoff: null,
    needs_you: [],
    working: [],
    world_changed: [],
    ...overrides,
  };
}

function activity(overrides: Partial<AgentActivityResponse> = {}): AgentActivityResponse {
  return {
    running: [],
    recent_events: [],
    heartbeat: { enabled: false },
    ...overrides,
  };
}

const mission = {
  id: "mis-1",
  title: "验证动量假设",
  question: "4h 动量有边缘吗?",
  phase: "investigation" as const,
  subject_type: "market" as const,
  subject_id: "BTC/USDT",
  updated_at: new Date().toISOString(),
};

describe("TodayView — SELENE IS WORKING 接实时活动", () => {
  it("有进行中的调查会 → 显示问题标题与已跑时长,不再显示 mission 行", () => {
    render(
      <TodayView
        data={today({ working: [mission] })}
        loading={false}
        activity={activity({
          running: [
            {
              question_id: "q-abc",
              question_title: "SOL 4h 亏损根因",
              question_status: "open",
              started_at: new Date(Date.now() - 125_000).toISOString(),
            },
          ],
        })}
      />,
    );
    expect(screen.getByText("SOL 4h 亏损根因")).toBeInTheDocument();
    expect(screen.getByText(/调查会 · 2m/)).toBeInTheDocument();
    expect(screen.getByText("1 个调查会进行中")).toBeInTheDocument();
    // running 优先:同一 mission 的回退行不该同时出现
    expect(screen.queryByText("验证动量假设")).not.toBeInTheDocument();
  });

  it("无 running 但有 mission 进行中 → 退回 mission 行", () => {
    render(
      <TodayView
        data={today({ working: [mission] })}
        loading={false}
        activity={activity()}
      />,
    );
    expect(screen.getByText("验证动量假设")).toBeInTheDocument();
    expect(screen.getByText("1 项进行中")).toBeInTheDocument();
  });

  it("最近动作流只显示前 5 条,视角名转人话,点进问题详情", () => {
    const events = Array.from({ length: 8 }, (_, i) => ({
      source: "question" as const,
      id: i + 1,
      question_id: `q-${i}`,
      mission_id: null,
      question_title: `问题 ${i}`,
      occurred_at: new Date().toISOString(),
      actor: "selene",
      kind: "investigate_perspective",
      detail: {
        perspective: i % 2 === 0 ? "empiricist" : "challenger",
        status: "ok",
        excerpt: `摘要 ${i}`,
      },
    }));
    render(
      <TodayView
        data={today()}
        loading={false}
        activity={activity({ recent_events: events })}
      />,
    );
    const panel = screen.getByTestId("recent-activity");
    expect(panel.querySelectorAll("a")).toHaveLength(5);
    // 5 条里经验派 3 条、挑战派 2 条(事件 i=0..4)
    expect(screen.getAllByText("经验派")).toHaveLength(3);
    expect(screen.getAllByText("挑战派")).toHaveLength(2);
    expect(screen.queryByText("问题 5")).not.toBeInTheDocument();
  });

  it("mission 侧活动渲染为人话,并链到 mission 详情", () => {
    render(
      <TodayView
        data={today()}
        loading={false}
        activity={activity({
          recent_events: [
            {
              source: "mission",
              id: 7,
              question_id: null,
              mission_id: "mis-abc",
              question_title: "PA diagnosis BTC/USDT 1h",
              occurred_at: new Date().toISOString(),
              actor: "selene",
              kind: "worker_analysis",
              detail: {},
            },
          ],
        })}
      />,
    );
    // 裸 kind 字符串不该出现在界面上
    expect(screen.queryByText("worker_analysis")).not.toBeInTheDocument();
    expect(screen.getByText("分析已出")).toBeInTheDocument();
    const row = screen
      .getByText("PA diagnosis BTC/USDT 1h")
      .closest("a") as HTMLAnchorElement | null;
    expect(row?.getAttribute("href")).toBe("/research/missions?id=mis-abc");
  });

  it("心跳启用时显示忙碌/空闲读数", () => {
    render(
      <TodayView
        data={today()}
        loading={false}
        activity={activity({
          heartbeat: { enabled: true, busy: true, attempts_today: 4 },
        })}
      />,
    );
    expect(screen.getByText("诊断中")).toBeInTheDocument();
  });

  it("心跳未启用 → 不渲染心跳行(关闭的特性不留空壳)", () => {
    render(<TodayView data={today()} loading={false} activity={activity()} />);
    expect(screen.queryByText(/PA 心跳/)).not.toBeInTheDocument();
  });

  it("聚合端点取不到(activity=null)→ 退回 mission 读数,不崩", () => {
    render(<TodayView data={today({ working: [mission] })} loading={false} activity={null} />);
    expect(screen.getByText("验证动量假设")).toBeInTheDocument();
    expect(screen.queryByTestId("recent-activity")).not.toBeInTheDocument();
  });

  it("NEEDS YOU 溢出提示链到真实存在的 Mission 一览,不指向不存在的 Portfolio", () => {
    // 旧文案「其余 N 条在研究线 Portfolio 查看」:该路由不存在(manifest 里
    // portfolio 已 absorbedBy: "pos",落到交易持仓页),而且整句没有链接,
    // 用户既点不进去也找不到完整列表。
    // 5 个不同的问句 → 5 组,折叠不会把它们并成一张。
    const many = Array.from({ length: 5 }, (_, i) => ({
      ...mission,
      id: `mis-${i}`,
      title: `待拍板 ${i}`,
      question: `问句 ${i}?`,
    }));
    render(<TodayView data={today({ needs_you: many })} loading={false} activity={activity()} />);
    expect(screen.queryByText(/Portfolio/)).not.toBeInTheDocument();
    const link = screen.getByText("研究线 · Mission 一览").closest("a") as HTMLAnchorElement | null;
    expect(link?.getAttribute("href")).toBe("/research/missions");
  });

  it("同一「标的 + 问句」的重复开案合成一张卡,标题不同也照折", () => {
    // 实测库里 26 条 open Mission 是同一个问句、三种标题(心跳「X PA 心跳诊断」
    // / CLI「PA diagnosis X 1h」/ 早期标题写 4h 而问句是 1h)。按标题分组对它们
    // 完全失明,NEEDS YOU 窗口里出现三对一模一样的待办。
    const dup = Array.from({ length: 4 }, (_, i) => ({
      ...mission,
      id: `mis-dup-${i}`,
      title: i % 2 === 0 ? "PA diagnosis BTC/USDT 1h" : "BTC/USDT PA 心跳诊断",
      question: "当前 1h 结构是什么状态?是否满足入场质量(gate)?",
      updated_at: new Date(Date.now() - i * 60_000).toISOString(),
    }));
    render(<TodayView data={today({ needs_you: dup })} loading={false} activity={activity()} />);

    // 只有一张卡(×4),不是四张;卡片用最新那条的标题,其余三种写法收进折叠
    expect(screen.getAllByText("PA diagnosis BTC/USDT 1h")).toHaveLength(1);
    expect(screen.queryByText("BTC/USDT PA 心跳诊断")).not.toBeInTheDocument();
    expect(screen.getByText("×4")).toBeInTheDocument();
    expect(screen.getByText(/另有 3 条同问句开案/)).toBeInTheDocument();
    // 段头口径:件数按行数(4),组数按折叠后(1)
    expect(screen.getByText("4 件待拍板 · 1 组")).toBeInTheDocument();
  });

  it("溢出按组计:折叠后的组超出 3 才提示,报的是组数不是行数", () => {
    // 3 组 × 各 2 条重复 = 6 行;组数正好 3,不该出现"还有"提示。
    const rows = Array.from({ length: 3 }, (_, g) =>
      Array.from({ length: 2 }, (_, k) => ({
        ...mission,
        id: `mis-g${g}-${k}`,
        title: `主题 ${g}`,
        question: `问句 ${g}?`,
        updated_at: new Date(Date.now() - (g * 2 + k) * 60_000).toISOString(),
      })),
    ).flat();
    render(<TodayView data={today({ needs_you: rows })} loading={false} activity={activity()} />);
    expect(screen.queryByText(/还有 \d+ 组/)).not.toBeInTheDocument();
    expect(screen.getByText("6 件待拍板 · 3 组")).toBeInTheDocument();
  });

  it("七环的「问句」读问题表真值,不与「使命」同数", () => {
    // 旧写法两环共用 openCount:数据源不同、数字相同,看着像计数坏了。
    // counts 缺失时必须显 —,不许回退到 mission 计数重新制造这个假象。
    const { unmount } = render(
      <TodayView
        data={today({ working: [mission], counts: { open_questions: 36 } })}
        loading={false}
        activity={activity()}
      />,
    );
    // 环的内部结构是 <span>标签<span>数字</span></span>,取标签的直接父节点
    // 就只读到这一个环,不会被别的环的数字污染。
    expect(screen.getByText("问句").parentElement?.textContent).toBe("问句36");
    expect(screen.getByText("使命").parentElement?.textContent).toBe("使命1");
    unmount();

    render(<TodayView data={today({ working: [mission] })} loading={false} activity={activity()} />);
    expect(screen.getByText("问句").parentElement?.textContent).toBe("问句—");
  });
});
