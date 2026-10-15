"use client";

import { Suspense } from "react";

import { DeskPage } from "@/components/research/desk-page";

export default function Desk() {
  return (
    <Suspense fallback={<div className="h-full" />}>
      <DeskPage />
    </Suspense>
  );
}
