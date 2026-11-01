import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent, waitFor } from "@testing-library/react";
import { EpisodeDrawer } from "@/components/research/episode-drawer";
import {
  getEpisode,
  getSubjectDerivation,
  transitionEpisode,
  type EpisodeDetail,
} from "@/lib/api/research-graph";

vi.mock("@/lib/api/research-graph", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/api/research-graph")
  >("@/lib/api/research-graph");
  return {
    ...actual,
    getEpisode: vi.fn(),
    getSubjectDerivation: vi.fn(),
    transitionEpisode: vi.fn(),
  };
});

const mockGet = vi.mocked(getEpisode);
const mockDerivation = vi.mocked(getSubjectDerivation);
const mockTransition = vi.mocked(transitionEpisode);

const episode = (
  status: string,
  transitions: EpisodeDetail["episode"]["recorded"]["lifecycle"] = undefined,
): EpisodeDetail => ({
  episode: {
    id: "ep-1",
    subject_type: "factor",
    subject_id: "funding_z",
    title: "funding_z 探针 BTC 1h",
    recorded: {
      probe: "funding_ic",
      lifecycle:
        transitions ??
        ({ status, transitions: [] } as NonNullable<
          EpisodeDetail["episode"]["recorded"]["lifecycle"]
        >),
    },
    occurred_at: "2026-09-18T08:00:00",
  },
  evidence: [],
});

beforeEach(() => {
  mockGet.mockReset();
  mockDerivation.mockReset();
  mockDerivation.mockResolvedValue({
    subject_type: "factor",
    subject_id: "funding_z",
    chain: [],
    latest: null,
    retired_in_history: false,
    suggestions: [],
  });
  mockTransition.mockReset();
});

describe("EpisodeDrawer", () => {
  it("渲染 lifecycle 徽章与事件流", async () => {
    mockGet.mockResolvedValue(
      episode("DISCOVERED", {
        status: "DISCOVERED",
        transitions: [
          {
            from: "DISCOVERED",
            to: "DISCOVERED",
            at: "2026-09-18T08:00:00",
            actor: "system",
          },
        ],
      }),
    );
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    expect(
      within(drawer).getByTestId("episode-lifecycle-badge"),
    ).toHaveTextContent("周期:新发现");
    expect(
      within(drawer).getByTestId("lifecycle-transition-row"),
    ).toHaveTextContent("DISCOVERED");
  });

  it("RETIRED 徽章置灰且无流转按钮", async () => {
    mockGet.mockResolvedValue(
      episode("RETIRED", {
        status: "RETIRED",
        transitions: [
          {
            from: "LIVE",
            to: "RETIRED",
            at: "2026-09-20T08:00:00",
            actor: "user",
            reason: "IC 衰减",
          },
        ],
      }),
    );
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    expect(
      within(drawer).getByTestId("episode-lifecycle-badge"),
    ).toHaveTextContent("周期:已退役");
    expect(
      within(drawer).queryByTestId("transition-inspected"),
    ).not.toBeInTheDocument();
    expect(
      within(drawer).queryByTestId("transition-retired"),
    ).not.toBeInTheDocument();
  });

  it("加载失败显示错误文案,不渲染流转按钮", async () => {
    mockGet.mockRejectedValue(new Error("404"));
    render(<EpisodeDrawer episodeId="ep-ghost" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    await waitFor(() =>
      expect(within(drawer).getByText(/加载失败/)).toBeInTheDocument(),
    );
    expect(
      within(drawer).queryByTestId("transition-retired"),
    ).not.toBeInTheDocument();
  });

  it("退役不带 reason 前端拦截(不发请求);带 reason POST 成功后刷新", async () => {
    mockGet.mockResolvedValue(episode("DISCOVERED"));
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    fireEvent.click(within(drawer).getByTestId("transition-retired"));
    const submit = await within(drawer).findByTestId("retire-submit");
    expect(submit).toBeDisabled();
    expect(mockTransition).not.toHaveBeenCalled();

    fireEvent.change(within(drawer).getByTestId("retire-reason-input"), {
      target: { value: "IC 衰减" },
    });
    expect(submit).toBeEnabled();

    // POST 成功后整体重拉(transitions 以响应后的 getEpisode 为准)
    mockGet.mockResolvedValue(
      episode("RETIRED", {
        status: "RETIRED",
        transitions: [
          {
            from: "DISCOVERED",
            to: "RETIRED",
            at: "2026-09-21T08:00:00",
            actor: "user",
            reason: "IC 衰减",
          },
        ],
      }),
    );
    mockTransition.mockResolvedValue({ episode: {} as EpisodeDetail["episode"] });
    fireEvent.click(submit);
    await waitFor(() => expect(mockTransition).toHaveBeenCalledTimes(1));
    expect(mockTransition).toHaveBeenCalledWith("ep-1", "RETIRED", "IC 衰减");
    await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(2));
    expect(
      await within(drawer).findByTestId("episode-lifecycle-badge"),
    ).toHaveTextContent("周期:已退役");
  });

  it("非法流转 400 显示错误文案不清空状态", async () => {
    mockGet.mockResolvedValue(episode("DISCOVERED"));
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    mockTransition.mockRejectedValue(new Error("400"));
    fireEvent.click(within(drawer).getByTestId("transition-inspected"));
    await waitFor(() =>
      expect(within(drawer).getByTestId("transition-error")).toBeInTheDocument(),
    );
    // 状态未清空:徽章仍是 DISCOVERED
    expect(
      within(drawer).getByTestId("episode-lifecycle-badge"),
    ).toHaveTextContent("周期:新发现");
  });

  it("funding_ic payload 渲染 IC 表(summary 置顶+因子行+trend 着色)", async () => {
    const detail = episode("DISCOVERED");
    detail.evidence = [
      {
        id: "ev-1",
        episode_id: "ep-1",
        mode: "RECORDED",
        payload: {
          summary: "funding_ic BTCUSDT 1h: d0=+0.0312",
          probe: "funding_ic",
          symbol: "BTCUSDT",
          interval: "1h",
          family: {
            threshold_abs: 0.02,
            n_candidates: 2,
            n_sig: 1,
            n_attempts: 3,
            binom_p: 0.271,
            exceeds_random_baseline: false,
          },
          candidates: {
            funding_z: {
              overall_mean_ic: 0.0312,
              recent_90d_mean_ic: 0.0401,
              trend_90d_vs_prior: 0.0089,
            },
            vol_z: {
              overall_mean_ic: 0.012,
              recent_90d_mean_ic: -0.003,
              trend_90d_vs_prior: -0.015,
            },
          },
        },
        source_ref: "probe:funding_ic",
        created_at: "2026-09-18T08:00:00",
      },
    ];
    mockGet.mockResolvedValue(detail);
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    const panel = within(drawer).getByTestId("probe-payload-panel");
    expect(panel).toHaveTextContent("funding_ic BTCUSDT 1h: d0=+0.0312");
    const table = within(panel).getByTestId("ic-candidates-table");
    const rows = within(table).getAllByTestId("ic-candidates-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("funding_z");
    expect(rows[0]).toHaveTextContent("+0.0312");
    expect(rows[1]).toHaveTextContent("vol_z");
    expect(within(drawer).queryByTestId("delay-rows")).not.toBeInTheDocument();
    expect(within(panel).getByTestId("payload-family")).toHaveTextContent(
      "家族已试 3 次",
    );
    expect(within(panel).getByTestId("payload-family")).toHaveTextContent(
      "过线 1/2",
    );
  });

  it("entry_delay_ic payload 渲染 delay 行+半衰+target definition", async () => {
    const detail = episode("DISCOVERED");
    detail.evidence = [
      {
        id: "ev-2",
        episode_id: "ep-1",
        mode: "RECORDED",
        payload: {
          summary: "entry_delay_ic BTCUSDT 1h: d0=+0.05(n=400); half_delay=2",
          probe: "entry_delay_ic",
          symbol: "BTCUSDT",
          interval: "1h",
          candidates: {
            n_bars: 400,
            delay_0: { funding_z: { ic: 0.05, n: 400 }, n_valid_bars: 400 },
            delay_1: { funding_z: { ic: 0.03, n: 400 }, n_valid_bars: 400 },
            delay_2: { funding_z: { ic: 0.02, n: 396 }, n_valid_bars: 396 },
            ic_half_delay_funding_z: 2,
          },
          target: { definition: "close[t+delay+8]/close[t+delay]-1" },
        },
        source_ref: "probe:entry_delay_ic",
        created_at: "2026-09-18T08:00:00",
      },
    ];
    mockGet.mockResolvedValue(detail);
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    const panel = within(drawer).getByTestId("probe-payload-panel");
    expect(
      within(panel).getByTestId("payload-target-definition"),
    ).toHaveTextContent("close[t+delay+8]/close[t+delay]-1");
    const delayRows = within(panel).getAllByTestId("delay-row");
    expect(delayRows).toHaveLength(3);
    expect(delayRows[0]).toHaveTextContent("d0");
    expect(delayRows[2]).toHaveTextContent("n=396");
    expect(within(panel).getByTestId("half-delay")).toHaveTextContent("半衰档:d2");
    expect(within(drawer).queryByTestId("ic-candidates-table")).not.toBeInTheDocument();
  });

  it("payload 缺 target 不渲染 target 行;缺 candidates 只渲染 summary", async () => {
    const detail = episode("DISCOVERED");
    detail.evidence = [
      {
        id: "ev-3",
        episode_id: "ep-1",
        mode: "RECORDED",
        payload: {
          summary: "funding_ic ETHUSDT 1h: 无结构",
          probe: "funding_ic",
          symbol: "ETHUSDT",
          interval: "1h",
        },
        source_ref: "probe:funding_ic",
        created_at: "2026-09-18T08:00:00",
      },
    ];
    mockGet.mockResolvedValue(detail);
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    const panel = within(drawer).getByTestId("probe-payload-panel");
    expect(panel).toHaveTextContent("funding_ic ETHUSDT 1h: 无结构");
    expect(
      within(panel).queryByTestId("payload-target-definition"),
    ).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("payload-family")).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("ic-candidates-table")).not.toBeInTheDocument();
    expect(within(panel).queryByTestId("delay-rows")).not.toBeInTheDocument();
  });

  it("非 RECORDED evidence 不渲染 payload 面板", async () => {
    const detail = episode("DISCOVERED");
    detail.evidence = [
      {
        id: "ev-4",
        episode_id: "ep-1",
        mode: "REVIEW",
        payload: { summary: "不该出现", probe: "funding_ic" },
        source_ref: "review:manual",
        created_at: "2026-09-18T08:00:00",
      },
    ];
    mockGet.mockResolvedValue(detail);
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    await within(drawer).findByTestId("episode-lifecycle-badge");
    expect(
      within(drawer).queryByTestId("probe-payload-panel"),
    ).not.toBeInTheDocument();
  });

  it("重跑链:derived_from 子→父顺序渲染", async () => {
    const detail = episode("DISCOVERED");
    detail.edges = [
      {
        id: "edge-1",
        from_type: "episode",
        from_id: "ep-1",
        to_type: "episode",
        to_id: "ep-0",
        kind: "derived_from",
      },
    ];
    mockGet.mockImplementation(async (id: string) => {
      if (id === "ep-1") return detail;
      return { ...detail, episode: { ...detail.episode, id: "ep-0" }, edges: [] };
    });
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    const chainEl = await within(drawer).findByTestId("derived-chain");
    expect(chainEl).toHaveTextContent("ep-1 → ep-0");
    expect(within(drawer).queryByTestId("chain-truncated")).not.toBeInTheDocument();
  });

  it("单 episode 无 derived_from 边时不渲染重跑链", async () => {
    mockGet.mockResolvedValue(episode("DISCOVERED"));
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    await within(drawer).findByTestId("episode-lifecycle-badge");
    expect(within(drawer).queryByTestId("derived-chain")).not.toBeInTheDocument();
  });

  it("分段一致率列渲染:3/4 与 — (无方向)分别正确", async () => {
    const detail = episode("DISCOVERED");
    detail.evidence = [
      {
        id: "ev-sp",
        episode_id: "ep-1",
        mode: "RECORDED",
        payload: {
          summary: "funding_ic 分段",
          probe: "funding_ic",
          candidates: {
            n_bars: 400,
            funding_z: {
              overall_mean_ic: 0.03,
              recent_90d_mean_ic: 0.02,
              trend_90d_vs_prior: 0.01,
              subperiod_sign_consistency: {
                n_subperiods: 4,
                n_nonempty: 4,
                consistent: 3,
                sign_ratio: 0.75,
              },
            },
            vol_z: {
              overall_mean_ic: -0.01,
              recent_90d_mean_ic: -0.01,
              trend_90d_vs_prior: 0.0,
              // 无方向:sign_ratio None → 显示 "—"
              subperiod_sign_consistency: {
                n_subperiods: 4,
                n_nonempty: 0,
                consistent: 0,
                sign_ratio: null,
              },
            },
          },
        },
        source_ref: "probe:funding_ic",
        created_at: "2026-09-18T08:00:00",
      },
    ];
    mockGet.mockResolvedValue(detail);
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    const cells = within(drawer).getAllByTestId("ic-row-subperiod");
    expect(cells[0]).toHaveTextContent("3/4");
    expect(cells[1]).toHaveTextContent("—");
  });

  it("无分段字段时显示 —(向后兼容旧 payload)", async () => {
    const detail = episode("DISCOVERED");
    detail.evidence = [
      {
        id: "ev-old",
        episode_id: "ep-1",
        mode: "RECORDED",
        payload: {
          summary: "旧形状",
          probe: "funding_ic",
          candidates: {
            n_bars: 400,
            funding_z: {
              overall_mean_ic: 0.03,
              recent_90d_mean_ic: 0.02,
              trend_90d_vs_prior: 0.01,
            },
          },
        },
        source_ref: "probe:funding_ic",
        created_at: "2026-09-18T08:00:00",
      },
    ];
    mockGet.mockResolvedValue(detail);
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    // 不抛错,渲染 "—"
    const cell = within(drawer).getByTestId("ic-row-subperiod");
    expect(cell).toHaveTextContent("—");
  });

  it("F3: replay_overlap 渲染重看提示(有键才显示)", async () => {
    const detail = episode("DISCOVERED");
    detail.evidence = [
      {
        id: "ev-replay",
        episode_id: "ep-1",
        mode: "RECORDED",
        payload: {
          summary: "entry_delay_ic 重跑",
          probe: "entry_delay_ic",
          replay_overlap: {
            ratio: 0.9945392491467577,
            prior_episode_count: 2,
            latest_prior_episode_id: "ep-ad71faf262d6",
          },
        },
        source_ref: "probe:entry_delay_ic",
        created_at: "2026-10-03T00:00:00",
      },
    ];
    mockGet.mockResolvedValue(detail);
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    const note = within(drawer).getByTestId("payload-replay-overlap");
    expect(note).toHaveTextContent("99.5%");
    expect(note).toHaveTextContent("不构成样本外确认");
    expect(note).toHaveTextContent("ep-ad71faf262d6");
    expect(note).toHaveTextContent("共 2 次");
  });

  it("F3: 无 replay_overlap 键时不渲染提示(向后兼容)", async () => {
    const detail = episode("DISCOVERED");
    detail.evidence = [
      {
        id: "ev-noreplay",
        episode_id: "ep-1",
        mode: "RECORDED",
        payload: {
          summary: "首次运行",
          probe: "entry_delay_ic",
        },
        source_ref: "probe:entry_delay_ic",
        created_at: "2026-10-03T00:00:00",
      },
    ];
    mockGet.mockResolvedValue(detail);
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    expect(
      within(drawer).queryByTestId("payload-replay-overlap"),
    ).not.toBeInTheDocument();
  });

  it("推导建议横幅:SUSPICIOUS 链建议显示复查+依据 episode", async () => {
    mockGet.mockResolvedValue(episode("DISCOVERED"));
    mockDerivation.mockResolvedValue({
      subject_type: "factor",
      subject_id: "funding_z",
      chain: [
        {
          episode_id: "ep-2",
          occurred_at: "2026-10-03T00:00:00",
          status: "DISCOVERED",
          verdict: "SUSPICIOUS",
        },
      ],
      latest: {
        episode_id: "ep-2",
        occurred_at: "2026-10-03T00:00:00",
        status: "DISCOVERED",
        verdict: "SUSPICIOUS",
      },
      retired_in_history: false,
      suggestions: [
        {
          action: "inspect",
          basis_episode_id: "ep-2",
          reason: "最新重测 verdict=SUSPICIOUS(IC 衰减证据,建议复查)",
        },
      ],
    });
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    const banner = await within(drawer).findByTestId("derivation-banner");
    expect(banner).toHaveTextContent("链建议:复查");
    expect(banner).toHaveTextContent("(依据 ep-2)");
    // 建议横幅不自动触发任何流转按钮(流转仍由人点)
    expect(mockTransition).not.toHaveBeenCalled();
  });

  it("无建议与灰区标记均不渲染横幅", async () => {
    mockGet.mockResolvedValue(episode("DISCOVERED"));
    mockDerivation.mockResolvedValue({
      subject_type: "factor",
      subject_id: "funding_z",
      chain: [],
      latest: null,
      retired_in_history: false,
      suggestions: [],
    });
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    await within(drawer).findByTestId("episode-lifecycle-badge");
    expect(within(drawer).queryByTestId("derivation-banner")).not.toBeInTheDocument();
    expect(
      within(drawer).queryByTestId("derivation-gray-zone"),
    ).not.toBeInTheDocument();
  });

  it("灰区:链中曾有 RETIRED 显示复活提示", async () => {
    mockGet.mockResolvedValue(episode("DISCOVERED"));
    mockDerivation.mockResolvedValue({
      subject_type: "factor",
      subject_id: "funding_z",
      chain: [
        {
          episode_id: "ep-2",
          occurred_at: "2026-10-05T00:00:00",
          status: "DISCOVERED",
          verdict: "VALID",
        },
        {
          episode_id: "ep-0",
          occurred_at: "2026-10-01T00:00:00",
          status: "RETIRED",
          verdict: "VALID",
        },
      ],
      latest: {
        episode_id: "ep-2",
        occurred_at: "2026-10-05T00:00:00",
        status: "DISCOVERED",
        verdict: "VALID",
      },
      retired_in_history: true,
      suggestions: [],
    });
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    const gray = await within(drawer).findByTestId("derivation-gray-zone");
    expect(gray).toHaveTextContent("复活与否请人工判断");
    expect(within(drawer).queryByTestId("derivation-banner")).not.toBeInTheDocument();
  });

  it("推导请求失败静默降级:抽屉主体照常渲染", async () => {
    mockGet.mockResolvedValue(episode("DISCOVERED"));
    mockDerivation.mockRejectedValue(new Error("500"));
    render(<EpisodeDrawer episodeId="ep-1" onClose={() => {}} />);
    const drawer = await screen.findByTestId("episode-drawer");
    await within(drawer).findByTestId("episode-lifecycle-badge");
    expect(within(drawer).queryByTestId("derivation-banner")).not.toBeInTheDocument();
    expect(
      within(drawer).queryByTestId("derivation-gray-zone"),
    ).not.toBeInTheDocument();
  });
});
