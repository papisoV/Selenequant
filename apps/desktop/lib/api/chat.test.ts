import { afterEach, describe, expect, it, vi } from "vitest";
import {
  listChatSessions,
  getChatSessionMessages,
  getChatSession,
  saveChatSessionMessages,
  deleteChatSession,
} from "./chat";

// 真契约回归:chat_sessions.py 是**裸响应**路由(返回 list/dict,不走 APIResponse 外壳)。
// 这些用例不 mock "@/lib/api/chat" 自己,只桩 fetch —— 所以走的是真实 api() 分流。
// 反向验证法:把 api-client 的 BARE_ENDPOINT_PATTERNS 里 chat 那条删掉,本文件必红。

vi.mock("@/lib/auth", () => ({
  getAccessToken: () => "test-token",
  clearAccessToken: () => {},
  refreshToken: () => Promise.resolve(false),
}));

/** 桩一个 JSON 响应(真后端形状:裸 body)。 */
function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SESSION = {
  id: "cs-6a89fd8e8729",
  strategy_id: "strat-4a1da8c4",
  title: "Help me write a trading strategy",
  created_at: "2026-05-08T05:08:59Z",
  updated_at: "2026-05-20T13:48:02Z",
  user_id: "local",
};

const MESSAGE = {
  id: "1778216939759-q1tu2ut",
  session_id: "cs-6a89fd8e8729",
  role: "user",
  content: "Help me write a trading strategy based on 1",
  created_at: "2026-05-20T13:48:02Z",
  extra: {},
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("chat session CRUD 对真后端裸响应", () => {
  it("listChatSessions 解析裸数组(非 envelope)", async () => {
    // 真机实录:GET /api/v1/chat/sessions → [{...}] 裸数组
    const fetchMock = vi.fn(async () => jsonResponse([SESSION]));
    vi.stubGlobal("fetch", fetchMock);

    const rows = await listChatSessions(undefined, 50);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(Array.isArray(rows)).toBe(true);
    expect(rows).toHaveLength(1);
    expect(rows[0].id).toBe("cs-6a89fd8e8729");
  });

  it("getChatSessionMessages 解析裸数组", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse([MESSAGE])));

    const msgs = await getChatSessionMessages("cs-6a89fd8e8729", 200);

    expect(Array.isArray(msgs)).toBe(true);
    expect(msgs[0].content).toContain("Help me write");
  });

  it("getChatSession 解析裸对象", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(SESSION)));

    const session = await getChatSession("cs-6a89fd8e8729");

    expect(session.id).toBe("cs-6a89fd8e8729");
    expect(session.title).toContain("Help me write");
  });

  it("saveChatSessionMessages PUT 裸 {status}", async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ status: "ok" }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await saveChatSessionMessages("cs-1", [{ id: "m1", role: "user", content: "hi" }]);

    expect(res).toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("deleteChatSession DELETE 裸 {status}", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse({ status: "ok" })));

    const res = await deleteChatSession("cs-1");

    expect(res).toEqual({ status: "ok" });
  });
});
