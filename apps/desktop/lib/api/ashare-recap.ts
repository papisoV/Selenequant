"use client";

import { ApiError } from "@/lib/api-client";
import { getAccessToken } from "@/lib/auth";

// A股 AI 大盘复盘 —— GET /api/v1/ashare/recap/stream(SSE)。
//
// 帧协议(服务端 ashare_overview.py):meta → delta* → done,任一阶段可先发 error。
// 与 market-analysis 的 analyze/stream 同构,但这里是 GET,且 error 是**协议内
// 事件**而非 HTTP 错误 —— 未配置 LLM 时后端照样 200 + error(llm_not_configured),
// 故必须按事件分派,不能只看 res.ok。
//
// 红线由服务端 SYSTEM_PROMPT 保证(禁用任何交易指令措辞),客户端**不得**
// 在 error 时补一段本地文本 —— 那等于伪造一份 AI 复盘。

const BASE = "/api/v1/ashare/recap/stream";

export interface RecapEventMeta {
  type: "meta";
  as_of: string | null;
  emotion_score: number | null;
  emotion_label: string | null;
  /** 服务端 recap_summary 生成的一行摘要(指数|情绪|涨停|成交额)。 */
  summary: string;
  status: string;
}

export type RecapEvent =
  | RecapEventMeta
  | { type: "delta"; content: string }
  | { type: "done" }
  | { type: "error"; message: string; reason?: string };

export interface RecapRequest {
  /** YYYY-MM-DD,空 = 当前交易日(服务端解析快照/日线底座的最新会话)。 */
  date?: string;
  /** 追加关注点;服务端会在 prompt 里重申红线,focus 无法绕过。 */
  focus?: string;
  /** 是否附龙虎榜上下文(多一次东财抓取)。 */
  lhb?: boolean;
}

/** 事件帧解析:按 ``data: {json}\n\n`` 切分(容忍跨 chunk 的帧边界)。 */
export function parseRecapFrame(frame: string): RecapEvent | null {
  const dataLine = frame.split("\n").find((line) => line.startsWith("data: "));
  if (!dataLine) return null;
  try {
    return JSON.parse(dataLine.slice("data: ".length)) as RecapEvent;
  } catch {
    return null;
  }
}

/**
 * 流式复盘:逐帧回调 onEvent;收到 done 正常返回。
 *
 * 收到 error 事件时**先回调再抛** ApiError(message, 500, {code: reason})——
 * UI 能保留已到达的 meta/正文,同时拿到服务端的原话(不自行改写)。
 * 流在未收到 done/error 时结束 = 异常终止,同样抛错,绝不当作成功。
 */
export async function streamAshareRecap(
  req: RecapRequest,
  onEvent: (ev: RecapEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const params = new URLSearchParams();
  if (req.date) params.set("date", req.date);
  if (req.focus?.trim()) params.set("focus", req.focus.trim());
  if (req.lhb === false) params.set("lhb", "false");
  const query = params.toString();
  const url = query ? `${BASE}?${query}` : BASE;

  const token = getAccessToken();
  const res = await fetch(url, {
    method: "GET",
    headers: {
      Accept: "text/event-stream",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    signal,
  });
  if (!res.ok || !res.body) {
    throw new ApiError(`HTTP ${res.status} ${res.statusText}`, res.status);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let settled = false;

  // 返回 "error" 表示已在帧内中断,调用方据终止标记退出读循环。
  const dispatchFrames = (chunk: string): "continue" | "error" => {
    buffer += chunk;
    let idx: number;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const ev = parseRecapFrame(frame);
      if (!ev) continue;
      if (ev.type === "error") {
        onEvent(ev);
        settled = true;
        throw new ApiError(ev.message, 500, { code: ev.reason });
      }
      onEvent(ev);
      if (ev.type === "done") settled = true;
    }
    return "continue";
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    dispatchFrames(decoder.decode(value, { stream: true }));
  }
  dispatchFrames(decoder.decode()); // flush 残留尾部
  dispatchFrames(""); // buffer 中未以 \n\n 结尾的最后一帧

  if (!settled) {
    // 没有 done 也没有 error = 连接中断,已到达的正文保留在调用方状态里。
    throw new ApiError("复盘流异常终止(未收到结束标记)", 500);
  }
}
