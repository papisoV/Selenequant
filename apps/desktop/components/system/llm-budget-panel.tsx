"use client";

import { useState } from "react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import {
  getLLMUsage,
  putLLMBudgetSettings,
  type LLMBudgetSettings,
  type LLMUsageState,
} from "@/lib/api/settings";
import { useUiText } from "@/components/i18n/use-ui-text";

// GET /api/v1/settings → data.llm_budget 段;GET /api/v1/settings/llm-usage →
// 当日实测用量。两个数字并排显示:上限(用户设的)与已用(真的花了多少)。
//
// 单位是 token,不是美元 —— 供应商回的是真实 token 用量,而本项目没有权威
// 价目表(只有 TokenCostTracker 里写死的 $0.25/M 猜测值)。编一个"今天花了
// $X"比不显示更糟:那正是这套改动要消灭的"看得见的假象"。
//
// 0 = 不设上限,也是本机默认。上限只拦无人值守的调用(PA 心跳 / 助手辅助
// 调用);用户正在打的对话不会被截断 —— 把正在看的人掐掉,比超支更糟。
// 展示名(中文源串) —— 渲染点过 tr();键名是后端协议值,不译。
const SOURCE_LABELS: Record<string, string> = {
  agent_chat: "对话",
  helper: "辅助调用",
  pa_analysis: "PA 诊断",
  pa_followup: "PA 追问",
};

interface SettingsData {
  llm_budget?: Partial<LLMBudgetSettings>;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function LLMBudgetPanel() {
  const tr = useUiText();
  const { useApi } = useApiPage();
  const { data, mutate } = useApi<SettingsData | null>("/api/v1/settings");
  const { data: usage, mutate: mutateUsage } = useApi<LLMUsageState | null>(
    "/api/v1/settings/llm-usage",
  );

  const saved: LLMBudgetSettings = {
    daily_tokens: Number(data?.llm_budget?.daily_tokens ?? 0),
  };
  const ready = data !== null;

  const [form, setForm] = useState<LLMBudgetSettings>(saved);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // 后端数据到达/更新时同步表单 —— render-adjust 模式(同 PaHeartbeatPanel)
  const [prevSaved, setPrevSaved] = useState<string>("");
  const savedKey = JSON.stringify(saved);
  if (savedKey !== prevSaved) {
    setPrevSaved(savedKey);
    setForm(saved);
  }

  const handleSave = async () => {
    if (!ready) {
      setMsg("数据加载中");
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      await putLLMBudgetSettings({
        daily_tokens: Math.max(0, Math.floor(Number(form.daily_tokens) || 0)),
      });
      setMsg("已保存,下一 tick 起生效");
      mutate();
      mutateUsage();
    } catch {
      setMsg("保存失败");
    } finally {
      setSaving(false);
      setTimeout(() => setMsg(null), 3000);
    }
  };

  const handleRefresh = async () => {
    try {
      const fresh = await getLLMUsage();
      // 写回同一个 SWR 缓存键,数字与后端同源,不在本地另算一份
      mutateUsage(fresh);
    } catch {
      setMsg("读取用量失败");
      setTimeout(() => setMsg(null), 3000);
    }
  };

  const exceeded = usage?.exceeded === true;
  const sources = usage ? Object.entries(usage.by_source) : [];

  return (
    <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
      <h2 className="set-group-title text-[12px] font-medium text-gold mb-1.5">
        {tr("每日 LLM 用量上限")}
      </h2>
      <div className="space-y-1.5">
        <div className="set-row flex items-center gap-2">
          <label htmlFor="llm-budget-daily" className="set-key text-ink-3 text-[11px] shrink-0">
            {tr("每日上限 (token)")}
          </label>
          <input
            id="llm-budget-daily"
            type="number"
            min={0}
            step={1000}
            value={form.daily_tokens}
            onChange={(e) => setForm({ daily_tokens: Number(e.target.value) })}
            className="set-input w-24 text-center"
          />
          <span className="text-ink-4 text-[11px]">{tr("0 = 不设上限")}</span>
        </div>

        <div className="set-row flex items-center justify-between gap-2">
          <span className="set-key text-ink-3 text-[11px]">{tr("今日已用")}</span>
          <span className="flex items-center gap-1.5">
            <span className={`font-mono text-[11px] ${exceeded ? "text-red-400" : "text-ink-2"}`}>
              {usage ? `${formatTokens(usage.tokens_today)} token` : "—"}
            </span>
            {usage && usage.daily_tokens > 0 && (
              <span className="text-ink-4 text-[11px]">
                / {formatTokens(usage.daily_tokens)}
              </span>
            )}
            <button
              type="button"
              onClick={handleRefresh}
              className="set-btn rounded border border-border-soft px-1.5 text-[11px] text-ink-3"
            >
              {tr("刷新")}
            </button>
          </span>
        </div>

        {usage && (
          <div className="set-row flex items-center justify-between gap-2">
            <span className="set-key text-ink-3 text-[11px]">{tr("今日调用")}</span>
            <span className="font-mono text-[11px] text-ink-3">
              {tr("{n} 次", { n: usage.calls_today })}
              {usage.day ? ` · ${usage.day} UTC` : ""}
            </span>
          </div>
        )}

        {exceeded && (
          <div className="text-red-400 text-[11px] leading-snug">
            {tr(
              "已达当日上限:无人值守的调用(PA 心跳、辅助调用)已暂停,对话仍可用。调高或清零上限后下一 tick 恢复。",
            )}
          </div>
        )}

        {sources.length > 0 && (
          <div className="text-ink-4 text-[11px] leading-snug">
            {tr("分布:")}
            {sources
              .sort((a, b) => b[1].total_tokens - a[1].total_tokens)
              .map(([key, v]) => `${tr(SOURCE_LABELS[key] ?? key)} ${formatTokens(v.total_tokens)}`)
              .join(" · ")}
          </div>
        )}

        <div className="text-ink-4 text-[11px] leading-snug">
          {tr(
            "按 token 计,不按美元 —— 供应商回的是真实用量,本项目没有权威价目表。上限只拦无人值守的调用;你正在进行的对话不会被截断。0 = 不设上限(本机默认),保存后下一分钟内生效,无需重启后端。",
          )}
        </div>
      </div>
      <div className="flex items-center gap-1.5 mt-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !ready}
          className="set-btn flex-1 rounded border border-gold bg-surface-3 px-2 text-[11px] text-gold disabled:opacity-50"
        >
          {saving ? tr("保存中...") : tr("保存")}
        </button>
        {msg && <span className="text-[11px] text-ink-3">{tr(msg)}</span>}
      </div>
    </div>
  );
}
