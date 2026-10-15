"use client";

import { usePathname } from "next/navigation";
import { useRouter } from "next/navigation";
import {
  useWorkflowStore,
  slugToStep,
  WORKFLOW_ORDER,
  stepToPath,
  type WorkflowStep,
} from "@/lib/store/workflow";

const STEP_LABELS: Record<WorkflowStep, string> = {
  strat: "策略",
  factors: "因子",
  optimize: "优化",
  backtest: "回测",
  deploy: "部署",
};

export function WorkflowStepper() {
  const pathname = usePathname();
  const router = useRouter();
  const slug = pathname.split("/").pop()?.split("?")[0] ?? "";
  const currentStep = slugToStep(slug);

  const template = useWorkflowStore((s) => s.template);
  const factors = useWorkflowStore((s) => s.factors);
  const optimization = useWorkflowStore((s) => s.optimization);
  const backtest = useWorkflowStore((s) => s.backtest);

  if (!currentStep) return null;

  const hasStep: Record<WorkflowStep, boolean> = {
    strat: template !== null,
    factors: factors !== null,
    optimize: optimization !== null,
    backtest: backtest !== null,
    deploy: backtest !== null,
  };

  return (
    <div className="flex items-center gap-1 px-3 py-1.5 text-[10px]">
      {WORKFLOW_ORDER.map((step, i) => {
        const isCurrent = step === currentStep;
        const done = hasStep[step] || WORKFLOW_ORDER.indexOf(step) < WORKFLOW_ORDER.indexOf(currentStep);
        return (
          <div key={step} className="flex items-center gap-1">
            {i > 0 && (
              <span className="text-ink-4 mx-0.5">›</span>
            )}
            <button
              onClick={() => router.push(stepToPath(step))}
              className={`rounded px-1.5 py-0.5 transition-colors ${
                isCurrent
                  ? "bg-gold/20 text-gold"
                  : done
                    ? "text-ink-2 hover:text-ink-1"
                    : "text-ink-4"
              }`}
            >
              {done && !isCurrent ? "✓ " : ""}
              {STEP_LABELS[step]}
            </button>
          </div>
        );
      })}
    </div>
  );
}