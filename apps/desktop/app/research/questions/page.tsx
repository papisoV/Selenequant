"use client";

import { Suspense } from "react";

import { QuestionSurface } from "@/components/research/question-surface";

export default function QuestionPage() {
  return (
    <Suspense fallback={<div className="h-full" />}>
      <QuestionSurface />
    </Suspense>
  );
}
