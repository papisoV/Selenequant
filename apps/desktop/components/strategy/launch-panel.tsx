"use client";

import { useState } from "react";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";
import type { DeploymentSummary } from "@/lib/api/strategies";
import type { StratTemplate } from "@/components/research/cards/factory-template-card";

/**
 * 策略广场启动面板 — 吸收 AICoin 转化漏斗:AICoin 先让用户填投入金额、
 * 再选策略、看参考收益/风控,最后才卡在授权。selene 无授权门槛,
 * 漏斗收窄为 选策略 → 看回测参考 → 启动。
 *
 * P0-1:原「投入 USDT」输入框已删除。理由:后端 DeployStrategyRequest
 * 只有 account_id、DeployLiveRequest 也无 capital 字段,金额无处下发;
 * 且 amount 甚至未参与右侧收益预估(仅用于预设按钮高亮)。保留一个
 * 填了不影响任何下游的输入 = UI 承诺不存在的影响,比没有更糟。
 * 若将来部署链路支持资金参数,再按真实契约加回。
 */
interface Props {
  templates: StratTemplate[];
  deployments: DeploymentSummary[];
  onDeployPaper: (t: StratTemplate) => Promise<void>;
  onDeployLive: (t: StratTemplate) => Promise<void>;
  // 原 deployingPaper/deployingLive 死 prop 已删(P3):调用点硬编码 null,
  // 而 handler 只瞬时开向导(busy 态活在 DeployWizardDialog 内),此 prop 永不可能为真。
}
export function LaunchPanel({
  templates,
  deployments,
  onDeployPaper,
  onDeployLive,
}: Props) {
  const [selectedId, setSelectedId] = useState("");

  const selected = templates.find((t) => t.id === selectedId);
  const running = deployments.filter((d) => d.status === "running");

  const sharpeCls = selected
    ? selected.sharpe >= 1.8 ? "text-pos" : selected.sharpe < 1.4 ? "text-neg" : "text-ink-2"
    : "text-ink-2";

  return (
    <V17Card title="启动机器人" right={<span className="text-[9px] text-ink-3">运行中 {running.length}</span>}>
      <div className="space-y-2">
        <div>
          <div className="text-[9px] text-ink-3 mb-1">选择策略</div>
          {templates.length === 0 ? (
            <div className="text-[10px] text-ink-4 py-2">暂无策略模板</div>
          ) : (
            <select
              value={selectedId}
              onChange={(e) => setSelectedId(e.target.value)}
              className="w-full rounded border border-border-soft bg-surface-2 px-2 py-1 text-[10px] text-ink outline-none"
              aria-label="选择策略"
            >
              <option value="">请选择策略…</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          )}
        </div>

        {selected && (
          <div className="grid grid-cols-3 gap-1">
            <StatBlock label="回测夏普" value={selected.sharpe.toFixed(2)} cls={sharpeCls} />
            <StatBlock label="回测胜率" value={`${selected.winrate}%`} />
            <StatBlock label="分类" value={selected.cat} />
          </div>
        )}

        <div className="flex gap-1 pt-1">
          <button
            type="button"
            onClick={() => selected && onDeployPaper(selected)}
            disabled={!selected}
            className="row-btn flex-1 rounded border border-gold/40 bg-gold/10 px-2 py-1.5 text-[10px] text-gold disabled:opacity-40"
          >
            启动 Paper
          </button>
          <button
            type="button"
            onClick={() => selected && onDeployLive(selected)}
            disabled={!selected}
            className="row-btn flex-1 rounded border border-pos/40 bg-pos/10 px-2 py-1.5 text-[10px] text-pos disabled:opacity-40"
          >
            启动 Live
          </button>
        </div>
        {!selected && (
          <div className="text-center text-[9px] text-ink-4 py-1">请先选择策略以启动</div>
        )}
      </div>
    </V17Card>
  );
}
