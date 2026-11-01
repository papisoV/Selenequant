"use client";

import { useState } from "react";
import type { UseApiResult } from "@/lib/hooks/use-api-page";
import type { StrategyStateResponse } from "./strategy-types";
import {
  ErrorsSection,
  EvolutionSection,
  HistorySection,
  IntelSection,
  NowSection,
  StratHeader,
} from "./strategy-workspace";
import { ReplayView } from "./strategy-replay-view";
import { StrategyWorkbenchPreview } from "./strategy-workbench-preview";
import { useUiText } from "@/components/i18n/use-ui-text"

type StateApi = UseApiResult<StrategyStateResponse>;

/** 渐进披露折叠段 — 标题行常显(空间记忆:位置固定),内容点开才展开;顺序不因折叠改变 */
function CollapseSection({
  label,
  count,
  open,
  onToggle,
  children,
}: {
  label: string;
  count?: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const tr = useUiText()

  return (
    <section className="rounded-lg border border-border bg-surface-1">
      <button
        type="button"
        onClick={onToggle}
        className="flex w-full items-baseline gap-2 px-3 py-2.5 text-left"
      >
        <span className="text-[11px] font-semibold tracking-wider text-ink">{label}</span>
        {count && <span className="text-[10px] text-ink-3">{count}</span>}
        <span className="ml-auto text-[10px] text-ink-3">{open ? tr("收起 ▲") : tr("展开 ▼")}</span>
      </button>
      {open && <div className="px-3 pb-3">{children}</div>}
    </section>
  );
}

/**
 * STRATEGY 驾驶舱组合视图 — ?id=strat-xxx 时渲染。
 * workspace(六段) ⇄ replay(错误调查现场全页)切换;episodes 点入即进 T 冻结现场。
 */
export function StrategyDetailView({ id, api }: { id: string; api: StateApi }) {
  const tr = useUiText()

  const [view, setView] = useState<"workspace" | "replay" | "workbench">("workspace");
  const [ep, setEp] = useState<number | null>(null);
  // 渐进披露:NOW/HISTORY 常显(主视图+生命轨迹),深观察层默认折叠
  const [openErrors, setOpenErrors] = useState(false);
  const [openIntel, setOpenIntel] = useState(false);
  const [openEvo, setOpenEvo] = useState(false);

  const d = api.data;
  // bt- 前缀 = 回测记录 id,不是策略 id。两条失败路径(404 进 error 分支 / 无数据)共用此提示。
  const backtestIdHint = (
    <>
      {tr("这是一个回测记录 id(")}<span className="num">{id}</span>{tr("),不是策略 id — 驾驶舱只展示策略。 这个回测关联的 Mission 调查记录在 MISSION 页,去看看这个回测暴露了什么问题。")}
    </>
  );

  if (api.isLoading && d === null) {
    return (
      <div className="mx-auto flex w-full max-w-[820px] flex-col items-start gap-2 p-4">
        <div className="flex h-48 w-full items-center justify-center text-[11px] text-ink-3">
          {tr("Strategy State 加载中…")}
        </div>
      </div>
    );
  }

  if (api.error) {
    // 三条失败路径必须分开报,否则会把业务"查无此策略"错报成"服务没起":
    //   1. bt- 前缀 + 404 = 回测记录 id(不是策略)
    //   2. 其它 404      = 主体不是有效策略(脏 Mission subject / 手输错 id)
    //   3. 无 HTTP 状态  = fetch 根本没到服务器(真·连接失败)
    const status = api.error.status;
    const isConnectivity = typeof status !== "number";
    return (
      <div className="mx-auto flex w-full max-w-[820px] flex-col items-start gap-2 p-4">
        <div className="w-full rounded-lg border border-neg/40 bg-surface-2 p-3.5 text-[11px] leading-relaxed text-ink-2">
          {isConnectivity ? (
            <>
              {tr("State 端点失败(")}{api.error.message}{tr(")— 确认")} <span className="num">local-api</span> {tr("已启动 (")}<span className="num">localhost:8088</span>{tr(")后刷新。")}
            </>
          ) : status === 404 && id.startsWith("bt-") ? (
            backtestIdHint
          ) : status === 404 ? (
            <>
              <span className="num">{id}</span> {tr("不是已注册策略,驾驶舱无状态可展示 — 这个主体 id 可能来自 Mission subject 挂错(如探针/回测 id)。 去")} <span className="num">MISSION</span> {tr("页核对该 Mission 的 subject 是否正确。")}
            </>
          ) : (
            <>
              {tr("State 端点失败(")}{api.error.message}{tr(")— 服务已响应但报错, 稍后重试或查看")} <span className="num">local-api</span> {tr("日志。")}
            </>
          )}
        </div>
      </div>
    );
  }

  if (!d) {
    return (
      <div className="mx-auto flex w-full max-w-[820px] flex-col items-start gap-2 p-4">
        <div className="w-full rounded-lg border border-border-soft bg-surface-2/50 p-3.5 text-[11px] leading-relaxed text-ink-3">
          {id.startsWith("bt-") ? (
            backtestIdHint
          ) : (
            <>
              {tr("未找到该策略的状态数据(")}<span className="num">{id}</span>{tr(")— 确认 Mission subject 指向的策略 id 正确。")}
            </>
          )}
        </div>
      </div>
    );
  }

  const eps = d.episodes_pack.episodes;
  const total = eps.length;

  const enterReplay = (n: number) => {
    if (total === 0) return;
    setEp(Math.min(Math.max(n, 1), total));
    setView("replay");
  };

  if (view === "replay" && ep !== null) {
    return (
      <div className="mx-auto w-full max-w-[1100px] p-4 pb-6">
        <ReplayView
          strategyId={id}
          episodeN={ep}
          total={total}
          onEpisodeChange={setEp}
          onExit={() => {
            setView("workspace");
            setEp(null);
          }}
        />
      </div>
    );
  }

  if (view === "workbench") {
    return <StrategyWorkbenchPreview id={id} onExit={() => setView("workspace")} />;
  }

  return (
    <div className="mx-auto w-full max-w-[820px] p-4 pb-6">
      <div className="flex flex-col gap-2.5">
        <StratHeader state={d} />
        <div className="flex gap-1.5" role="tablist" aria-label={tr("策略视图")}>
          <button
            type="button"
            role="tab"
            aria-selected
            onClick={() => setView("workspace")}
            className="rounded-md border border-accent/40 bg-accent/10 px-2.5 py-1.5 text-[11px] font-medium text-ink transition-colors"
          >
            {tr("驾驶舱")}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={false}
            onClick={() => setView("workbench")}
            className="rounded-md border border-transparent px-2.5 py-1.5 text-[11px] font-medium text-ink-3 transition-colors hover:text-ink"
          >
            {tr("工作台")}
          </button>
        </div>
        <NowSection state={d} onReplay={enterReplay} />
        <HistorySection episodes={eps} selected={null} onSelect={enterReplay} />
        <CollapseSection
          label={tr("ERRORS · 哪里失败过")}
          count={tr("{n} 笔可回放", { n: eps.length })}
          open={openErrors}
          onToggle={() => setOpenErrors((v) => !v)}
        >
          <ErrorsSection state={d} onReplay={enterReplay} />
        </CollapseSection>
        <CollapseSection
          label="INTELLIGENCE · Strategy Knowledge"
          count={d.missions.some((mi) => mi.phase === "resolved") ? tr("1 条已结案知识") : tr("暂无")}
          open={openIntel}
          onToggle={() => setOpenIntel((v) => !v)}
        >
          <IntelSection state={d} />
        </CollapseSection>
        <CollapseSection
          label={tr("EVOLUTION · 下一版从哪里来")}
          open={openEvo}
          onToggle={() => setOpenEvo((v) => !v)}
        >
          <EvolutionSection state={d} />
        </CollapseSection>
      </div>
    </div>
  );
}
