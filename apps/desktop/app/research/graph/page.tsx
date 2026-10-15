"use client";

import { Suspense } from "react";

import { GraphViewPanel } from "@/components/research/graph-view-panel";

export default function ResearchGraphPage() {
  return (
    <Suspense fallback={<div className="h-full" />}>
      <GraphViewPanel />
    </Suspense>
  );
}
