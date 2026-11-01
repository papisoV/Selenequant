# Hooks 迁移指南: useApi → useApiPage

`useApiPage` 是页面级 SWR hook 工厂,支持显式声明 `distinguish` 策略(区分服务挂/真无数据)。
`useApi`(旧)已 `@deprecated`,新代码强制用 `useApiPage`。

## 三步迁移

### 1. 改 import

```ts
// 旧
import { useApi } from "@/lib/hooks/use-api";

// 新
import { useApiPage } from "@/lib/hooks/use-api-page";
```

### 2. 调 useApiPage({distinguish})

在组件顶层(无条件调用,符合 hooks 规则):

```ts
// 选项 A: 区分服务挂 / 真无数据(推荐用于关键页面:trd / positions / orders)
const { useApi } = useApiPage({ distinguish: true });

// 选项 B: 维持现状(错误吞 null,与旧 useApi 行为一致,渐进迁移用)
const { useApi } = useApiPage({ distinguish: false });
// 或省略:const { useApi } = useApiPage();
```

### 3. 用返回的 useApi(签名兼容旧版)

```ts
// 旧
const { data, error, isLoading } = useApi<T>(path, { refreshInterval: 1000 });

// 新(签名一样,额外返回 mutate)
const { data, error, isLoading, mutate } = useApi<T>(path, { refreshInterval: 1000 });
```

## distinguish 选择指南

| 场景 | distinguish | 理由 |
|---|---|---|
| 交易/持仓/订单页(trd / positions / orders) | `true` | 服务挂要可见,不能当"无数据"展示 |
| 自选/列表页(watchlist / sig / gex) | `false` | 渐进迁移,避免现有空态逻辑被破坏 |
| 不确定 | `false` | 与旧行为一致,迁移零风险 |

## distinguish=true 时组件如何区分

```ts
const { data, error, isLoading } = useApi<T>(path, ...);

if (error) {
  // 服务挂(HTTP 5xx / 网络 / 4xx)→ ApiError 抛到这里
  return <ErrorState error={error} />;
}
if (isLoading) return <Loading />;
if (data === null) {
  // 真无数据(后端返回空 / "No X data available" sentinel)
  return <EmptyState />;
}
return <DataView data={data} />;
```

## 示例:trd-page 迁移

```ts
// 迁移前
import { useApi } from "@/lib/hooks/use-api";
export function TrdPage() {
  const { data } = useApi<Positions>("/api/v1/trading/positions");
  // ...
}

// 迁移后
import { useApiPage } from "@/lib/hooks/use-api-page";
export function TrdPage() {
  const { useApi } = useApiPage({ distinguish: true });
  const { data, error } = useApi<Positions>("/api/v1/trading/positions");
  if (error) return <ErrorState error={error} />;
  // ...
}
```

## ESLint 规则

`eslint.config.mjs` 配置了 `no-restricted-imports`(warn 级):
- `@/lib/hooks/use-api` import 会触发 warning
- 43 页存量暂时保留 warning,渐进迁移
- 新代码不允许新增该 import

## 注意事项

- `useApiPage` 返回的 `useApi` 不能用 `useCallback` 包(hooks 不能 memoize)
- `distinguish` 在 `useApiPage` 闭包捕获,每次渲染产生新 `useApi` 函数引用,但 SWR 按 path 缓存,不会重渲染
- `mutate` 是新增返回值,旧代码可忽略
