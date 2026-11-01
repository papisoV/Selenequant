// 多副本后端 host 解析,统一直连/WS 的后端地址来源。
// 优先级:
//   1. NEXT_PUBLIC_API_HOST —— 显式覆盖(兼容历史,如战术面板/监控条直连)
//   2. NEXT_PUBLIC_LOCAL_API_BASE —— next.config.ts env 注入的 LOCAL_API_BASE
//      (多副本主开关,与 rewrite 同源:一个 env 全通)
//   3. 默认 127.0.0.1:8088 —— 对齐 uvicorn 绑定,防 localhost→::1 IPv6 断链
//      (Chromium 里 localhost 先解析 ::1,uvicorn 只绑 IPv4,并发连接会卡死在 ::1 连接池)
export function resolveBackendHost(): string {
  const explicit = process.env.NEXT_PUBLIC_API_HOST;
  if (explicit) return explicit;

  const viaBase = process.env.NEXT_PUBLIC_LOCAL_API_BASE;
  if (viaBase) return viaBase.replace(/^https?:\/\//, "");

  return "127.0.0.1:8088";
}
