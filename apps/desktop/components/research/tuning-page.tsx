"use client";

import { TuningControlPanel } from "@/components/research/tuning-control-panel";
import { TuningResultsPanel } from "@/components/research/tuning-results-panel";

export function TuningPage() {
  return (
    <div className="h-full overflow-y-auto"><div className="space-y-4">
      <TuningControlPanel />
      <TuningResultsPanel />
      </div>
    </div>
  );
}
