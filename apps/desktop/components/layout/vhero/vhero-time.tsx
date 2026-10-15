"use client";

import { useEffect, useState } from "react";

function getMarketState(now: Date): { label: string; cls: string } {
  const hours = now.getUTCHours();
  const day = now.getUTCDay();
  const isWeekend = day === 0 || day === 6;
  if (isWeekend) return { label: "CRYPTO ONLY", cls: "crypto" };
  if (hours >= 0 && hours < 1) return { label: "A-SHARE PRE", cls: "premarket" };
  if (hours >= 1 && hours < 7) return { label: "A-SHARE OPEN", cls: "open" };
  if (hours >= 13 && hours < 14) return { label: "US PRE", cls: "premarket" };
  if (hours >= 14 && hours < 21) return { label: "US OPEN", cls: "open" };
  return { label: "CRYPTO 24H", cls: "crypto" };
}

export function VheroTime() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  if (!now) {
    return (
      <div className="vhero-time">
        <span className="vt-clock">--:--</span>
        <span className="vt-market">···</span>
      </div>
    );
  }
  const hh = String(now.getUTCHours()).padStart(2, "0");
  const mm = String(now.getUTCMinutes()).padStart(2, "0");
  const market = getMarketState(now);
  return (
    <div className="vhero-time">
      <span className="vt-clock">{hh}:{mm}</span>
      <span className="vt-market">
        <span className={`mk-dot ${market.cls}`} />
        {market.label}
      </span>
    </div>
  );
}
