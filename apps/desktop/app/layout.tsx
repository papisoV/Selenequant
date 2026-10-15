import type { Metadata } from "next";
import { ClientProviders } from "@/components/client-providers";

// 字体自托管(@fontsource 本地包)——构建期零外网依赖。
// 之前用 next/font/google 会在构建/编译时从 fonts.googleapis.com 拉 woff2,
// 大陆无代理环境永远拉不到,全站静默退化成 local(Arial)。
// Noto Sans SC 走 chinese-simplified 子集(7946 码点,含 ASCII/CJK/标点),4 字重 ≈ 4.4MB。
import "@fontsource/noto-sans-sc/chinese-simplified-400.css";
import "@fontsource/noto-sans-sc/chinese-simplified-500.css";
import "@fontsource/noto-sans-sc/chinese-simplified-600.css";
import "@fontsource/noto-sans-sc/chinese-simplified-700.css";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-500.css";
import "@fontsource/jetbrains-mono/latin-600.css";

import "./globals.css";

export const metadata: Metadata = {
  title: "SeleneQuant",
  description:
    "AI Quant Research System。上一个问题的真实研究结果,会进入下一个问题的判断。",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body><ClientProviders>{children}</ClientProviders></body>
    </html>
  );
}
