/// <reference types="vite/client" />

declare module "*.vue" {
  import type { DefineComponent } from "vue";

  const component: DefineComponent<Record<string, never>, Record<string, never>, any>;
  export default component;
}

interface QaAgentDesktopBridge {
  isDesktop: boolean;

  notify(payload: {
    title: string;
    body: string | undefined;
    tag: string | undefined;
    silent: boolean | null | undefined;
  }): Promise<boolean>;

  setZoomFactor(factor: number): Promise<number>;

  openFlowWindow(payload?: {
    sessionId?: string;
    turnId?: string;
  }): Promise<boolean>;
}

interface Window {
  qaAgentDesktop?: QaAgentDesktopBridge;
}
