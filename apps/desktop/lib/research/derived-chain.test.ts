import { describe, it, expect, vi } from "vitest";

import { walkDerivedChain, type DerivedEdge } from "@/lib/research/derived-chain";

const edgesOf = (
  ...pairs: readonly [string, string][]
): ((id: string) => Promise<DerivedEdge[]>) => {
  const edges: DerivedEdge[] = pairs.map(([from, to]) => ({ from_id: from, to_id: to }));
  return async (id) => edges.filter((e) => e.from_id === id);
};

describe("walkDerivedChain", () => {
  it("线性链 A→B→C 全量返回且不截断", async () => {
    const r = await walkDerivedChain("A", edgesOf(["A", "B"], ["B", "C"]));
    expect(r).toEqual({ chain: ["A", "B", "C"], truncated: false });
  });

  it("无父(首跑 episode)只返回自己", async () => {
    const r = await walkDerivedChain("A", edgesOf());
    expect(r).toEqual({ chain: ["A"], truncated: false });
  });

  it("环 A→B+B→A 终止并返回部分链", async () => {
    const r = await walkDerivedChain("A", edgesOf(["A", "B"], ["B", "A"]));
    expect(r).toEqual({ chain: ["A", "B"], truncated: false });
  });

  it("超 maxHops 截断且 truncated=true", async () => {
    const fetchEdges = vi.fn(edgesOf(["A", "B"], ["B", "C"], ["C", "D"]));
    const r = await walkDerivedChain("A", fetchEdges, 2);
    expect(r.chain).toEqual(["A", "B", "C"]);
    expect(r.truncated).toBe(true);
  });

  it("恰好 maxHops 到底不误报截断", async () => {
    const r = await walkDerivedChain("A", edgesOf(["A", "B"], ["B", "C"]), 2);
    expect(r).toEqual({ chain: ["A", "B", "C"], truncated: false });
  });
});
