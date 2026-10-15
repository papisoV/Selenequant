"use client";

import { AuthProvider } from "@/components/auth/AuthProvider";
import { I18nProvider } from "@/components/i18n/i18n-provider";
import { BackendReadyProvider } from "@/components/providers/backend-ready-provider";
import { MarketsWsProvider } from "@/lib/ws/markets-ws-provider";
import { useGlobalShortcuts } from "@/lib/hooks/use-global-shortcuts";
import { usePageviewRecorder } from "@/lib/hooks/use-pageview-recorder";

function GlobalShortcutsBinder() {
  useGlobalShortcuts();
  return null;
}

function PageviewBinder() {
  usePageviewRecorder();
  return null;
}

export function ClientProviders({ children }: { children: React.ReactNode }) {
  return (
    <I18nProvider>
      <AuthProvider>
        <BackendReadyProvider>
          <MarketsWsProvider>
            <GlobalShortcutsBinder />
            <PageviewBinder />
            {children}
          </MarketsWsProvider>
        </BackendReadyProvider>
      </AuthProvider>
    </I18nProvider>
  );
}
