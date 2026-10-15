/**
 * research-graph 客户端测试 —— F1 回归门的客户端半边。
 *
 * 关键纪律:mock 的是 fetch,不是 apiPost —— 喂后端真实的原始 shape,
 * 才能测到 fetchEnvelope 的真实解包行为。若日后有人把 fetchEnvelope
 * 改成形状探测,反向断言(裸 dict → null)会红并强制他回来改这里。
 *
 * 后端半边由 pytest(test_api_research_graph.py)与裸 dict grep 门把守;
 * 本文件不碰真实路由,别把它当成全链路的门。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  addRevision,
  createQuestion,
  recallLedger,
  getQuestion,
  closeQuestion,
  reopenQuestion,
  investigateQuestion,
} from "./research-graph";

const ENVELOPE = (data: unknown) =>
  JSON.stringify({ data, meta: { request_id: "req-test" }, error: null });

describe("research-graph client", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(ENVELOPE({ ok: true }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ) as unknown as typeof fetch,
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("listQuestions 打 GET /api/v1/research/graph/questions", async () => {
    await getQuestion("q-1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/research/graph/questions/q-1",
      expect.objectContaining({ headers: expect.anything() }),
    );
  });

  it("createQuestion 打 POST /api/v1/research/graph/questions,body 是 JSON", async () => {
    await createQuestion({ title: "SOL 4h 亏损根因" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/research/graph/questions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ title: "SOL 4h 亏损根因" }),
      }),
    );
  });

  it("addRevision 打 POST revisions,body 带 cited", async () => {
    await addRevision("q-1", { content: "第二版", cited: ["rev-a"] });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/research/graph/questions/q-1/revisions",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ content: "第二版", cited: ["rev-a"] }),
      }),
    );
  });

  it("closeQuestion/reopenQuestion 打各自的 close/reopen 端点", async () => {
    await closeQuestion("q-1", { terminal_status: "parked" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/research/graph/questions/q-1/close",
      expect.objectContaining({ method: "POST" }),
    );
    await reopenQuestion("q-1", { reason: "新证据" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/research/graph/questions/q-1/reopen",
      expect.objectContaining({ method: "POST" }),
    );
  });

  // ---- F1 回归门 ---------------------------------------------------------

  it("F1 正向:fetch 喂后端真实信封 shape,recallLedger 解出非 null 且含 candidates", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          ENVELOPE({
            candidates: [
              {
                id: "rev-a",
                kind: "revision",
                text: "第一版回答",
                question_id: "q-1",
                score: 1,
                matched_on: ["question:same"],
                recall_class: "research_record",
                evidence_chain: [],
                created_at: "2026-09-18T00:00:00+00:00",
              },
            ],
            learned: 0,
            records: 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ) as unknown as typeof fetch,
    );
    const result = await recallLedger({ question_id: "q-1" });
    expect(result).not.toBeNull();
    expect(result!.candidates).toHaveLength(1);
    expect(result!.candidates[0].id).toBe("rev-a");
    // 候选来源单一:recall 请求体默认带 LEDGER_KINDS
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls.at(-1)!;
    expect(JSON.parse(init.body)).toMatchObject({
      question_id: "q-1",
      kinds: ["revision", "hypothesis", "evidence"],
    });
  });

  it("F1 正向:createQuestion 解出的 question_id 非 null", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(ENVELOPE({ question_id: "q-new-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ) as unknown as typeof fetch,
    );
    const result = await createQuestion({ title: "t" });
    expect(result).not.toBeNull();
    expect(result!.question_id).toBe("q-new-1");
  });

  it("F1 反向:喂裸 dict shape(不带 data 信封)→ 解包结果为 null", async () => {
    // 这条钉住"为什么后端必须包 APIResponse":fetchEnvelope 无条件解包
    // wrapped.data,裸 dict 没有 data 键 → null。若有人改 fetchEnvelope
    // 为形状探测,此断言会红,需同步更新本测试与后端契约。
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(JSON.stringify({ candidates: [{ id: "rev-a" }] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ) as unknown as typeof fetch,
    );
    const result = await recallLedger({ question_id: "q-1" });
    expect(result).toBeNull();
  });

  it("recallLedger 打 POST /api/v1/research/graph/recall", async () => {
    await recallLedger({ question_id: "q-1" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/research/graph/recall",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("P3:investigateQuestion 打 POST investigate 端点,解出 202 触发回执", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          ENVELOPE({
            question_id: "q-1",
            status: "investigating",
          }),
          // 后端用 JSONResponse 手拼信封返回 202
          { status: 202, headers: { "Content-Type": "application/json" } },
        ),
      ) as unknown as typeof fetch,
    );
    const result = await investigateQuestion("q-1");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/research/graph/questions/q-1/investigate",
      expect.objectContaining({ method: "POST" }),
    );
    expect(result).not.toBeNull();
    expect(result!.question_id).toBe("q-1");
    expect(result!.status).toBe("investigating");
  });

  it("N1-F1 默认态:investigateQuestion 无 payload 时 body 是 {} 不含 as_of 键", async () => {
    await investigateQuestion("q-1");
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls.at(-1)!;
    expect(JSON.parse(init.body)).toEqual({});
  });

  it("N1-F1 回放态:payload.as_of 透传,body 恰好只含 as_of", async () => {
    await investigateQuestion("q-1", { as_of: "2026-10-05T23:59:59.000Z" });
    const [, init] = (fetch as ReturnType<typeof vi.fn>).mock.calls.at(-1)!;
    expect(JSON.parse(init.body)).toEqual({ as_of: "2026-10-05T23:59:59.000Z" });
  });
});
