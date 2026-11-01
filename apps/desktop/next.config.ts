import type { NextConfig } from "next";

// 多副本环境:每个副本各自起 local-api,用 LOCAL_API_BASE 指向本副本后端。
const apiBase = process.env.LOCAL_API_BASE ?? "http://127.0.0.1:8088";

const nextConfig: NextConfig = {
  // dev 代理默认 30s 掐死长请求(node_modules/next/.../proxy-request.js proxyTimeout || 30000),
  // investigate(council 2~5 分钟)会收到合成 500 而后端仍在跑 —— 放宽到 15 分钟。
  experimental: {
    proxyTimeout: 900_000,
  },
  // e2e 冒烟/本地浏览器经 127.0.0.1 访问 dev 资源,默认按同源拦截会 403 掉 chunk(见 Next 16 allowedDevOrigins)
  allowedDevOrigins: ["127.0.0.1", "localhost"],
  // 把 LOCAL_API_BASE 注入客户端,供 apiDirect/WS 直连复用(浏览器读不到无 NEXT_PUBLIC_ 前缀的 env)
  env: {
    NEXT_PUBLIC_LOCAL_API_BASE: apiBase,
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${apiBase}/api/:path*` },
      { source: "/ws/:path*", destination: `${apiBase}/ws/:path*` },
    ];
  },
};

export default nextConfig;
