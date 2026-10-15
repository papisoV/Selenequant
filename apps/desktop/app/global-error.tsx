"use client";

import { useState } from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const message = error?.message || "页面加载时发生错误，请重试。";
  const digest = error?.digest;

  const handleCopy = async () => {
    const text = [message, digest ? `digest: ${digest}` : null].filter(Boolean).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  return (
    <html>
      <body className="bg-background text-foreground">
        <div className="flex min-h-screen items-center justify-center">
          <div className="text-center max-w-md px-4">
            <h1 className="text-4xl font-bold tracking-tight mb-4">
              SeleneQuant
            </h1>
            <p className="text-neutral-400 mb-2 break-words">
              {message}
            </p>
            {digest && (
              <p className="text-xs text-neutral-500 mb-6 font-mono">
                digest: {digest}
              </p>
            )}
            {!digest && <div className="mb-6" />}
            <div className="flex gap-4 justify-center">
              <button
                onClick={() => reset()}
                className="px-6 py-2.5 rounded-full bg-white text-black font-medium hover:bg-neutral-200 transition-colors"
              >
                重试
              </button>
              <a
                href="/dashboard"
                className="px-6 py-2.5 rounded-full border border-neutral-700 text-white hover:bg-neutral-800 transition-colors"
              >
                进入平台
              </a>
              <button
                onClick={handleCopy}
                className="px-6 py-2.5 rounded-full border border-neutral-700 text-neutral-300 hover:bg-neutral-800 transition-colors"
              >
                {copied ? "已复制" : "复制错误"}
              </button>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}
