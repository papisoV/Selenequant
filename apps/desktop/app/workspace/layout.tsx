import type { Scene } from "@/lib/page-manifest";
import { DashboardLayout } from "@/components/layout/dashboard-layout";
import { WorkspaceLayoutSync } from "@/components/workspace/workspace-layout-sync";

export default function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  return (
    <DashboardLayout scene="workspace">
      <WorkspaceLayoutSync />
      {children}
    </DashboardLayout>
  );
}
