"use client";

import { useState } from "react";
import { useApiPage } from "@/lib/hooks/use-api-page";
import {
  putMissionWorkerSettings,
  putResearchAutorenewSettings,
  putResearchSensorSettings,
  type MissionWorkerSettings,
  type ResearchAutorenewSettings,
  type ResearchSensorSettings,
} from "@/lib/api/settings";

// 研究链路的两个无人值守循环。它们是一条链上的两段,所以放在同一张卡里 ——
// 单看任一段都读不懂"为什么开了却没动静":
//
//   问题 ──(自动续研)──▶ 修订 ──(自动开立)──▶ investigation mission
//                                                      │
//                                              (调查 worker)
//                                                      ▼
//                                            真回测 → needs_input
//
// **两段默认都关**(2026-10-26 起;worker 此前默认开)。理由见下面各自说明。
// 关掉不是"坏了":只是把推进权交回人手。
//
// 后端每 tick 重读这两节,所以保存后下一 tick 生效,不需要重启。
const WORKER_MIN_INTERVAL = 1;
const WORKER_MAX_INTERVAL = 1440;
const AUTORENEW_MIN_INTERVAL = 1;
const AUTORENEW_MAX_INTERVAL = 1440;
const SENSOR_MIN_INTERVAL = 1;
const SENSOR_MAX_INTERVAL = 1440;

interface SettingsData {
  mission_worker?: Partial<MissionWorkerSettings>;
  research_autorenew?: Partial<ResearchAutorenewSettings>;
  research_sensor?: Partial<ResearchSensorSettings>;
}

export function ResearchLoopsPanel() {
  const { useApi } = useApiPage();
  const { data, mutate } = useApi<SettingsData | null>("/api/v1/settings");

  const savedWorker: MissionWorkerSettings = {
    enabled: data?.mission_worker?.enabled === true,
    interval_minutes: Number(data?.mission_worker?.interval_minutes ?? 15),
    max_rounds_per_mission: Number(
      data?.mission_worker?.max_rounds_per_mission ?? 3,
    ),
  };
  const savedAutorenew: ResearchAutorenewSettings = {
    enabled: data?.research_autorenew?.enabled === true,
    interval_minutes: Number(data?.research_autorenew?.interval_minutes ?? 30),
    max_per_round: Number(data?.research_autorenew?.max_per_round ?? 3),
    auto_promote: data?.research_autorenew?.auto_promote !== false,
  };
  const savedSensor: ResearchSensorSettings = {
    enabled: data?.research_sensor?.enabled === true,
    interval_minutes: Number(data?.research_sensor?.interval_minutes ?? 60),
    question_limit: Number(data?.research_sensor?.question_limit ?? 200),
  };
  const ready = data !== null;

  const [worker, setWorker] = useState<MissionWorkerSettings>(savedWorker);
  const [autorenew, setAutorenew] =
    useState<ResearchAutorenewSettings>(savedAutorenew);
  const [sensor, setSensor] = useState<ResearchSensorSettings>(savedSensor);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  // 后端数据到达/更新时同步表单 —— render-adjust 模式(同 PaHeartbeatPanel)
  const [prevSaved, setPrevSaved] = useState<string>("");
  const savedKey = JSON.stringify([savedWorker, savedAutorenew, savedSensor]);
  if (savedKey !== prevSaved) {
    setPrevSaved(savedKey);
    setWorker(savedWorker);
    setAutorenew(savedAutorenew);
    setSensor(savedSensor);
  }

  const clamp = (value: number, lo: number, hi: number) =>
    Math.min(hi, Math.max(lo, Math.floor(Number(value) || lo)));

  const handleSave = async () => {
    if (!ready) {
      setMsg("数据加载中");
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      await putMissionWorkerSettings({
        enabled: worker.enabled,
        interval_minutes: clamp(
          worker.interval_minutes,
          WORKER_MIN_INTERVAL,
          WORKER_MAX_INTERVAL,
        ),
        max_rounds_per_mission: clamp(worker.max_rounds_per_mission, 1, 20),
      });
      await putResearchAutorenewSettings({
        enabled: autorenew.enabled,
        interval_minutes: clamp(
          autorenew.interval_minutes,
          AUTORENEW_MIN_INTERVAL,
          AUTORENEW_MAX_INTERVAL,
        ),
        max_per_round: clamp(autorenew.max_per_round, 1, 20),
        auto_promote: autorenew.auto_promote,
      });
      await putResearchSensorSettings({
        enabled: sensor.enabled,
        interval_minutes: clamp(
          sensor.interval_minutes,
          SENSOR_MIN_INTERVAL,
          SENSOR_MAX_INTERVAL,
        ),
        question_limit: clamp(sensor.question_limit, 1, 2000),
      });
      const anyOn = worker.enabled || autorenew.enabled || sensor.enabled;
      setMsg(anyOn ? "已保存,下一轮起生效" : "已保存,循环都停着");
      mutate();
    } catch {
      setMsg("保存失败");
    } finally {
      setSaving(false);
      setTimeout(() => setMsg(null), 4000);
    }
  };

  return (
    <div className="set-group rounded border border-border-soft bg-surface-2 p-2">
      <div className="set-group-title text-[10px] font-medium text-gold mb-1.5">
        研究循环(无人值守,默认全关)
      </div>

      {/* 第一段:调查 worker */}
      <div className="space-y-1.5 pb-2 border-b border-border-soft">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-[9px] text-ink">调查 worker</span>
          <button
            type="button"
            aria-label="调查 worker 启用"
            onClick={() => setWorker((p) => ({ ...p, enabled: !p.enabled }))}
            aria-pressed={worker.enabled}
            className={`set-btn font-mono px-2 rounded border text-[9px] ${
              worker.enabled
                ? "border-gold text-gold bg-surface-3"
                : "border-border-soft text-ink-4 bg-surface-2"
            }`}
          >
            {worker.enabled ? "ON" : "OFF"}
          </button>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="mw-interval" className="text-ink-3 text-[9px] shrink-0">
            轮询间隔(分钟)
          </label>
          <input
            id="mw-interval"
            aria-label="调查 worker 轮询间隔(分钟)"
            type="number"
            min={WORKER_MIN_INTERVAL}
            max={WORKER_MAX_INTERVAL}
            step={1}
            value={worker.interval_minutes}
            onChange={(e) =>
              setWorker((p) => ({
                ...p,
                interval_minutes: Number(e.target.value),
              }))
            }
            className="set-input w-16 text-center"
          />
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="mw-rounds" className="text-ink-3 text-[9px] shrink-0">
            单任务轮次上限
          </label>
          <input
            id="mw-rounds"
            aria-label="单任务轮次上限"
            type="number"
            min={1}
            max={20}
            step={1}
            value={worker.max_rounds_per_mission}
            onChange={(e) =>
              setWorker((p) => ({
                ...p,
                max_rounds_per_mission: Number(e.target.value),
              }))
            }
            className="set-input w-16 text-center"
          />
        </div>
        <div className="text-ink-4 text-[9px] leading-snug">
          把 investigation 相位的任务推进到 needs_input:取材料、真回测、写证据。
          每轮至少调一次模型(真花钱)。关着的时候新任务停在 investigation 等人,
          已推进过的不受影响。轮次用尽也会停下等人,不会自己下结论。
        </div>
      </div>

      {/* 第二段:自动续研(⑤→①) */}
      <div className="space-y-1.5 pt-2">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-[9px] text-ink">
            自动续研(结案 → 下一轮)
          </span>
          <button
            type="button"
            aria-label="自动续研启用"
            onClick={() =>
              setAutorenew((p) => ({ ...p, enabled: !p.enabled }))
            }
            aria-pressed={autorenew.enabled}
            className={`set-btn font-mono px-2 rounded border text-[9px] ${
              autorenew.enabled
                ? "border-gold text-gold bg-surface-3"
                : "border-ink-4 text-ink-4 bg-surface-2"
            }`}
          >
            {autorenew.enabled ? "ON" : "OFF"}
          </button>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="ar-interval" className="text-ink-3 text-[9px] shrink-0">
            轮询间隔(分钟)
          </label>
          <input
            id="ar-interval"
            aria-label="自动续研轮询间隔(分钟)"
            type="number"
            min={AUTORENEW_MIN_INTERVAL}
            max={AUTORENEW_MAX_INTERVAL}
            step={1}
            value={autorenew.interval_minutes}
            onChange={(e) =>
              setAutorenew((p) => ({
                ...p,
                interval_minutes: Number(e.target.value),
              }))
            }
            className="set-input w-16 text-center"
          />
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="ar-max" className="text-ink-3 text-[9px] shrink-0">
            每轮最多处理
          </label>
          <input
            id="ar-max"
            aria-label="每轮最多处理"
            type="number"
            min={1}
            max={20}
            step={1}
            value={autorenew.max_per_round}
            onChange={(e) =>
              setAutorenew((p) => ({
                ...p,
                max_per_round: Number(e.target.value),
              }))
            }
            className="set-input w-16 text-center"
          />
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-ink-3 text-[9px]">续研后自动开立任务</span>
          <button
            type="button"
            aria-label="续研后自动开立任务"
            onClick={() =>
              setAutorenew((p) => ({ ...p, auto_promote: !p.auto_promote }))
            }
            aria-pressed={autorenew.auto_promote}
            className={`set-btn font-mono px-2 rounded border text-[9px] ${
              autorenew.auto_promote
                ? "border-gold text-gold bg-surface-3"
                : "border-ink-4 text-ink-4 bg-surface-2"
            }`}
          >
            {autorenew.auto_promote ? "ON" : "OFF"}
          </button>
        </div>
        <div className="text-ink-4 text-[9px] leading-snug">
          只对结案派生的问题生效(带 derived_from 边),而且每个问题只自动
          续研一次 —— 不会指数增殖。它跨了"开调查是用户的按钮"这条红线,
          所以默认关:打开就是给系统的授权。关掉则只把新的子问题留在列表里等人点。
        </div>
        {autorenew.enabled && !worker.enabled && (
          <div className="text-[9px] text-ink-3 leading-snug">
            注意:两个是一对。自动开立的任务要由上面的 worker 推进;worker 关着时,
            这里只会把任务摆进 investigation 等人来推。
          </div>
        )}
      </div>

      {/* 第三段:① 传感器(只读扫描) */}
      <div className="space-y-1.5 pt-2 border-b border-border-soft">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-[9px] text-ink">行情传感器(只读)</span>
          <button
            type="button"
            aria-label="行情传感器开关"
            onClick={() => setSensor((p) => ({ ...p, enabled: !p.enabled }))}
            aria-pressed={sensor.enabled}
            className={`set-btn font-mono px-2 rounded border text-[9px] ${
              sensor.enabled
                ? "border-gold text-gold bg-surface-3"
                : "border-ink-4 text-ink-4 bg-surface-2"
            }`}
          >
            {sensor.enabled ? "ON" : "OFF"}
          </button>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="sensor-interval" className="text-ink-3 text-[9px] shrink-0">
            扫描间隔(分钟)
          </label>
          <input
            id="sensor-interval"
            aria-label="行情传感器扫描间隔(分钟)"
            type="number"
            min={SENSOR_MIN_INTERVAL}
            max={SENSOR_MAX_INTERVAL}
            step={1}
            value={sensor.interval_minutes}
            onChange={(e) =>
              setSensor((p) => ({
                ...p,
                interval_minutes: Number(e.target.value),
              }))
            }
            className="set-input w-16 text-center"
          />
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="sensor-limit" className="text-ink-3 text-[9px] shrink-0">
            每轮最多扫问题
          </label>
          <input
            id="sensor-limit"
            aria-label="每轮最多扫描问题数"
            type="number"
            min={1}
            max={2000}
            step={1}
            value={sensor.question_limit}
            onChange={(e) =>
              setSensor((p) => ({
                ...p,
                question_limit: Number(e.target.value),
              }))
            }
            className="set-input w-16 text-center"
          />
        </div>
        <div className="text-ink-4 text-[9px] leading-snug">
          定期扫 open 问题锚定的行情序列,按该序列自己的 99 分位判异动;触发
          才往问题挂一条市场观察证据。不花 LLM 钱,也绝不开立任务 —— 但会往
          图里写证据行,所以默认关。
        </div>
      </div>

      <div className="flex items-center gap-1.5 mt-2">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving || !ready}
          className="set-btn flex-1 rounded border border-gold bg-surface-3 px-2 text-[9px] text-gold disabled:opacity-50"
        >
          {saving ? "保存中..." : "保存"}
        </button>
        {msg && <span className="text-[9px] text-ink-3">{msg}</span>}
      </div>
    </div>
  );
}
