import type { Scene } from "@/lib/page-manifest";
import { DashboardLayout } from "@/components/layout/dashboard-layout";

export default function ResearchLayout({ children }: { children: React.ReactNode }) {
  return <DashboardLayout scene="research">{children}</DashboardLayout>;
}
