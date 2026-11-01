"use client";

import { FactorLifecyclePanel } from "@/components/research/factor-lifecycle-panel";
import { FactorResearchPanel } from "@/components/research/factor-research-panel";

export function FactorsPage() {
  return (
    <div className="h-full overflow-y-auto"><div className="space-y-6">
      <FactorLifecyclePanel />
      <FactorResearchPanel />
      </div>
    </div>
  );
}
