"use client";

import { useApi } from "@/lib/hooks/use-api";
import { V17Card, StatBlock } from "@/components/shared/page-filter-bar";

// 设计稿扩展页 — 聪明钱追踪
// 端点: /api/v1/onchain/smart-money
// 后端真实字段: { symbol, movements: [{ts, symbol, direction, value_usd, label, confidence, hash}], count, timestamp }
// direction ∈ {accumulate, distribute, transfer}
// label ∈ {smart_money_in, smart_money_out, exchange_transfer}
interface SmartMoneyMovement {
  ts?: string | number;
  symbol?: string;
  direction?: string;
  value_usd?: number;
  label?: string;
  confidence?: number;
  hash?: string;
}
interface SmartMoneyData {
  symbol?: string;
  movements?: SmartMoneyMovement[];
  count?: number;
  timestamp?: number;
}

const DIRECTION_LABEL: Record<string, string> = {
  accumulate: "吸纳",
  distribute: "派发",
  transfer: "转账",
};

export function SmartMoneyPage() {
  const { data } = useApi<SmartMoneyData | null>(
    "/api/v1/onchain/smart-money"
  );
  const d = data ?? null;
  const moves = d?.movements ?? [];

  const totalUsd = moves.reduce((s, m) => s + (m.value_usd ?? 0), 0);
  const inflowUsd = moves
    .filter((m) => m.direction === "accumulate")
    .reduce((s, m) => s + (m.value_usd ?? 0), 0);
  const outflowUsd = moves
    .filter((m) => m.direction === "distribute")
    .reduce((s, m) => s + (m.value_usd ?? 0), 0);
  const net = inflowUsd - outflowUsd;
  const maxUsd = Math.max(1, ...moves.map((m) => m.value_usd ?? 0));

  // 按 direction 聚合分类计数
  const bucket = (dir: string) =>
    moves.filter((m) => (m.direction ?? "") === dir).length;

  return (
    <div className="v17-page-grid grid gap-2">
      <V17Card title="聪明钱指标">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
          <StatBlock
            label="净流向(USD)"
            value={moves.length ? `${net >= 0 ? "+" : ""}${(net / 1e6).toFixed(2)}M` : "—"}
            cls={net >= 0 ? "text-pos" : "text-neg"}
          />
          <StatBlock
            label="流入总额"
            value={inflowUsd ? `${(inflowUsd / 1e6).toFixed(2)}M` : "—"}
            cls="text-pos"
          />
          <StatBlock
            label="流出总额"
            value={outflowUsd ? `${(outflowUsd / 1e6).toFixed(2)}M` : "—"}
            cls="text-neg"
          />
          <StatBlock label="动作总数" value={String(d?.count ?? moves.length)} />
        </div>
      </V17Card>

      <V17Card title="地址分类(按行为)">
        <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <StatBlock label="吸纳" value={String(bucket("accumulate"))} cls="text-pos" />
          <StatBlock label="派发" value={String(bucket("distribute"))} cls="text-neg" />
          <StatBlock label="转账" value={String(bucket("transfer"))} cls="text-gold" />
        </div>
      </V17Card>

      <V17Card title="Top 聪明钱动作">
        <div className="flex flex-col">
          {moves.length === 0 && (
            <div className="text-center text-ink-4 text-[11px] py-3">暂无数据</div>
          )}
          {moves.slice(0, 20).map((it, i) => {
            const isPos = it.direction === "accumulate";
            const isNeg = it.direction === "distribute";
            return (
              <div
                key={i}
                className="flex items-center gap-2 text-[10px] py-1 border-b border-border-soft"
              >
                <span
                  className={`w-12 font-medium ${isPos ? "text-pos" : isNeg ? "text-neg" : "text-gold"}`}
                >
                  {DIRECTION_LABEL[it.direction ?? ""] ?? it.direction ?? "-"}
                </span>
                <span className="w-14 text-ink-3 font-mono truncate">
                  {(it.hash ?? "-").slice(0, 10)}
                </span>
                <div className="flex-1 h-2 bg-surface-2 rounded overflow-hidden">
                  <div
                    className={`h-full ${isPos ? "bg-pos" : isNeg ? "bg-neg" : "bg-gold"}`}
                    style={{ width: `${((it.value_usd ?? 0) / maxUsd) * 100}%` }}
                  />
                </div>
                <span className="w-16 text-right text-ink-2 font-mono num">
                  {(it.value_usd ?? 0).toFixed(2)}
                </span>
                <span className="w-12 text-right text-ink-4 font-mono">
                  {it.confidence != null ? `${(it.confidence * 100).toFixed(0)}%` : "-"}
                </span>
              </div>
            );
          })}
        </div>
      </V17Card>
    </div>
  );
}
