"use client";

import { useState } from "react";

export interface CopyLeader {
  id: string;
  name: string;
  pnlPct: number;
  winRate: number;
  followers: number;
  strategy: string;
}

export const DEMO_LEADERS: CopyLeader[] = [
  { id: "ld-1", name: "Alpha Whale", pnlPct: 42.5, winRate: 68, followers: 1204, strategy: "动量突破" },
  { id: "ld-2", name: "Grid Master", pnlPct: 31.2, winRate: 74, followers: 892, strategy: "网格震荡" },
  { id: "ld-3", name: "DCA 老兵", pnlPct: 24.8, winRate: 81, followers: 567, strategy: "定投抄底" },
  { id: "ld-4", name: "资金费率猎手", pnlPct: 38.9, winRate: 63, followers: 1430, strategy: "期现套利" },
];

export interface CopyFollow {
  id: string;
  leaderName: string;
  amount: number;
  status: "模拟跟单中";
}

export function CopyTradePanel() {
  const [leaderId, setLeaderId] = useState(DEMO_LEADERS[0]?.id ?? "");
  const [amount, setAmount] = useState("1000");
  const [follows, setFollows] = useState<CopyFollow[]>([]);

  const follow = () => {
    const leader = DEMO_LEADERS.find((l) => l.id === leaderId);
    if (!leader) return;
    const amountN = Math.max(0, Number(amount) || 0);
    setFollows((prev) => [
      { id: `cf-${prev.length + 1}-${Date.now().toString(36)}`, leaderName: leader.name, amount: amountN, status: "模拟跟单中" },
      ...prev,
    ]);
  };

  return (
    <div className="copy-trade-panel flex flex-col gap-2">
      <div className="text-[9px] text-ink-4">跟单排行榜(纯演示数据)</div>
      <div className="flex flex-col gap-1">
        {DEMO_LEADERS.map((l) => (
          <div key={l.id} className="leader-row flex items-center gap-2 rounded border border-border-soft px-2 py-1 text-[11px]">
            <span className="w-28 truncate font-medium text-ink">{l.name}</span>
            <span className={`w-16 shrink-0 font-mono ${l.pnlPct >= 0 ? "text-pos" : "text-neg"}`}>{l.pnlPct}%</span>
            <span className="w-14 shrink-0 text-ink-3">{l.winRate}% 胜率</span>
            <span className="w-16 shrink-0 text-ink-3">{l.followers} 跟单</span>
            <span className="flex-1 truncate text-[9px] text-ink-4">{l.strategy}</span>
          </div>
        ))}
      </div>

      <div className="mt-1 grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          跟单对象
          <select aria-label="跟单对象" value={leaderId} onChange={(e) => setLeaderId(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink">
            {DEMO_LEADERS.map((l) => (
              <option key={l.id} value={l.id}>{l.name}</option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[10px] text-ink-3">
          跟单金额
          <input aria-label="跟单金额" type="number" value={amount} onChange={(e) => setAmount(e.target.value)}
            className="rounded border border-border-soft bg-surface-2 px-1 py-1 text-[11px] text-ink" />
        </label>
      </div>
      <button type="button" onClick={follow}
        className="w-fit rounded bg-gold px-2 py-1 text-[10px] font-medium text-black hover:opacity-90">
        开始跟单
      </button>

      {follows.length > 0 && (
        <div className="flex flex-col gap-1">
          <div className="text-[9px] text-ink-4">我的跟单</div>
          {follows.map((f) => (
            <div key={f.id} className="follow-row flex items-center gap-2 rounded border border-border-soft px-2 py-1 text-[11px]">
              <span className="w-28 truncate font-medium text-ink">{f.leaderName}</span>
              <span className="font-mono text-ink">{f.amount.toFixed(0)}</span>
              <span className="ml-auto text-[9px] text-gold">{f.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
