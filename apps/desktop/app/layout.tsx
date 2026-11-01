import type { Metadata } from "next";
import { ClientProviders } from "@/components/client-providers";
import { DEFAULT_LOCALE, LOCALE_STORAGE_KEY } from "@/lib/i18n";

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

/**
 * 首帧前就地把 <html lang/data-locale> 设成已存 locale —— 避免三件事:
 * 1. 语言闪烁(SSR 渲染默认 locale，挂载后才切到已存的)
 * 2. 涨跌色闪烁(globals.css 的 :root[data-locale="zh-CN"] 翻转块，
 *    A股用户首帧会看到绿涨红跌、下一帧才翻红涨绿)
 * 3. React hydration 不匹配(I18nProvider 的 effect 与 SSR 输出分叉)
 * 内联同步执行，无网络/无依赖，异常时静默保持 SSR 默认值。
 */
const LOCALE_BOOTSTRAP = `(function(){try{var l=localStorage.getItem(${JSON.stringify(
  LOCALE_STORAGE_KEY,
)});if(l==="en"||l==="zh-CN"){document.documentElement.lang=l;document.documentElement.dataset.locale=l}}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang={DEFAULT_LOCALE} data-locale={DEFAULT_LOCALE}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: LOCALE_BOOTSTRAP }} />
      </head>
      <body><ClientProviders>{children}</ClientProviders></body>
    </html>
  );
}
