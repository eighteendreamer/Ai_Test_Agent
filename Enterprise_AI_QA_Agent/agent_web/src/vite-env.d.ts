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

  recorder: {
    createWindow(payload: {
      recordingId: string;
      entryUrl: string;
      name?: string;
    }): Promise<{ recordingId: string; reused: boolean }>;

    navigate(recordingId: string, url: string): Promise<boolean>;

    attachDebugger(recordingId: string): Promise<boolean>;

    setCapture(recordingId: string, enabled: boolean): Promise<boolean>;

    captureScreenshot(recordingId: string): Promise<string | null>;

    close(recordingId: string): Promise<boolean>;

    getState(recordingId: string): Promise<{
      recordingId: string;
      attached: boolean;
      currentUrl: string;
      bufferedEvents: number;
      forwardedEvents: number;
      droppedMalformed: number;
    } | null>;
  };
}

interface Window {
  qaAgentDesktop?: QaAgentDesktopBridge;
}
