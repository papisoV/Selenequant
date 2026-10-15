/**
 * derived_from 重跑链 walk —— 子→父逐跳,纯函数(环防护 + 跳数上限)。
 *
 * derived_from 边语义(objects.link_derived_from):from=子 episode,
 * to=父 episode,同一 subject 的血缘只指向过去(rowid 严格早于)。
 * 正常库不会成环;防御性 visited-set 是防竞态/手写边的兜底。
 */

export interface DerivedEdge {
  from_id: string;
  to_id: string;
}

export interface WalkResult {
  chain: string[];
  truncated: boolean;
}

export async function walkDerivedChain(
  startId: string,
  fetchEdges: (episodeId: string) => Promise<DerivedEdge[]>,
  maxHops = 10,
): Promise<WalkResult> {
  const chain: string[] = [startId];
  const visited = new Set<string>([startId]);
  let current = startId;
  let truncated = false;

  for (let hop = 0; hop < maxHops; hop++) {
    const edges = await fetchEdges(current);
    const parent = edges.find((e) => e.from_id === current)?.to_id;
    if (!parent || visited.has(parent)) return { chain, truncated };
    visited.add(parent);
    chain.push(parent);
    current = parent;
  }
  // 走满 maxHops 还没到底:再探一跳,有父 = 真截断,无父 = 恰好到头
  const edges = await fetchEdges(current);
  const parent = edges.find((e) => e.from_id === current)?.to_id;
  truncated = Boolean(parent && !visited.has(parent));
  return { chain, truncated };
}
