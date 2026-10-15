// 前端 shape 类型 — 设计稿渲染函数期望的字段名
// 来源：_temp/final/demo-aa-scene-layout.html 的 mock 数据 + PAGE_RENDERERS

// Position（前端 shape）— 来源设计稿第 3798-3806 行 mock
export interface FrontendPosition {
  sym: string;
  side: "多" | "空" | string;
  qty: string;
  avg: string;
  now: string;
  pnl: string;
  cls: "pos" | "neg";
  market: string;
}

// PendingOrder（前端 shape）— 来源设计稿第 3790-3797 行 mock
export interface FrontendPendingOrder {
  sym: string;
  side: "买" | "卖" | string;
  qty: string;
  price: string;
  tif: string;
  status: string;
  market: string;
}

// ClosedPosition（前端 shape）— 来源设计稿第 3807-3817 行 mock
export interface FrontendClosedPosition {
  time: string;
  sym: string;
  side: string;
  qty: string;
  price: string;
  pnl: string;
  cls: "pos" | "neg";
  dur: string;
  market: string;
}

// Strategy（前端 shape）— 来源设计稿第 3891-3907 行 mock
export interface FrontendStrategy {
  id: string;
  name: string;
  cat: string;
  catLabel: string;
  sharpe: string;
  winrate: string;
  status: string;
}

// Account（前端 shape）— 来源设计稿第 3935-3939 行 mock
export interface FrontendAccount {
  id: string;
  icon: string;
  name: string;
  bal: string;
  status: string;
  active: boolean;
  cls: "pos" | "neg" | string;
}

// ChatSession（前端 shape）— 来源设计稿第 3920-3925 行 mock
export interface FrontendChatSession {
  id: string;
  title: string;
  meta: string;
  active: boolean;
}
