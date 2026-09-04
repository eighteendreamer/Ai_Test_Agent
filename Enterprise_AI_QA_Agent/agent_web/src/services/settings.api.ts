import type {
  EmailConfigPublic,
  EmailConfigCreateRequest,
  EmailConfigActionResponse,
  EmailConfigUpdateRequest,
  ChannelAdvancedSettings,
  ChannelGatewayDecision,
  ChannelGatewaySessionReleaseRequest,
  ChannelGatewaySessionReleaseResponse,
  ChannelInboundMessage,
  ChannelConfigActionResponse,
  ChannelConfigCreateRequest,
  ChannelConfigPublic,
  ChannelConfigUpdateRequest,
  ChannelDomain,
  ChannelPairingApproveRequest,
  ChannelPairingRequestPublic,
  ChannelPairingSessionPublic,
  ChannelPairingStartRequest,
  MailboxProviderInfo,
  MailboxSendConfirmRequest,
} from "../types";
import { request, readErrorMessage } from "./http";

export function listEmailConfigs(signal?: AbortSignal): Promise<EmailConfigPublic[]> {
  return request("/api/v1/settings/email", { signal });
}

export function createEmailConfig(payload: EmailConfigCreateRequest): Promise<EmailConfigPublic> {
  return request("/api/v1/settings/email", { method: "POST", body: JSON.stringify(payload) });
}

export function updateEmailConfig(configId: number, payload: EmailConfigUpdateRequest): Promise<EmailConfigPublic> {
  return request(`/api/v1/settings/email/${configId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function activateEmailConfig(configId: number): Promise<EmailConfigActionResponse> {
  return request(`/api/v1/settings/email/${configId}/activate`, { method: "POST" });
}

export function deleteEmailConfig(configId: number): Promise<EmailConfigActionResponse> {
  return request(`/api/v1/settings/email/${configId}`, { method: "DELETE" });
}

export function listChannelConfigs(): Promise<ChannelConfigPublic[]> {
  return request("/api/v1/settings/channels");
}

export function getChannelAdvancedSettings(): Promise<ChannelAdvancedSettings> {
  return request("/api/v1/settings/channels/advanced");
}

export function updateChannelAdvancedSettings(payload: ChannelAdvancedSettings): Promise<ChannelAdvancedSettings> {
  return request("/api/v1/settings/channels/advanced", { method: "PUT", body: JSON.stringify(payload) });
}

export function evaluateChannelInbound(payload: ChannelInboundMessage): Promise<ChannelGatewayDecision> {
  return request("/api/v1/settings/channels/gateway/evaluate", { method: "POST", body: JSON.stringify(payload) });
}

export function listChannelPairingRequests(): Promise<ChannelPairingRequestPublic[]> {
  return request("/api/v1/settings/channels/gateway/pairing");
}

export function approveChannelPairing(payload: ChannelPairingApproveRequest): Promise<ChannelPairingRequestPublic> {
  return request("/api/v1/settings/channels/gateway/pairing/approve", { method: "POST", body: JSON.stringify(payload) });
}

export function releaseChannelGatewaySession(
  payload: ChannelGatewaySessionReleaseRequest,
): Promise<ChannelGatewaySessionReleaseResponse> {
  return request("/api/v1/settings/channels/gateway/session/release", { method: "POST", body: JSON.stringify(payload) });
}

export function createChannelConfig(payload: ChannelConfigCreateRequest): Promise<ChannelConfigPublic> {
  return request("/api/v1/settings/channels", { method: "POST", body: JSON.stringify(payload) });
}

export function updateChannelConfig(configId: number, payload: ChannelConfigUpdateRequest): Promise<ChannelConfigPublic> {
  return request(`/api/v1/settings/channels/${configId}`, { method: "PATCH", body: JSON.stringify(payload) });
}

export function deleteChannelConfig(configId: number): Promise<ChannelConfigActionResponse> {
  return request(`/api/v1/settings/channels/${configId}`, { method: "DELETE" });
}

export function startChannelPairing(
  domain: ChannelDomain,
  payload: ChannelPairingStartRequest,
): Promise<ChannelPairingSessionPublic> {
  return request(`/api/v1/settings/channels/${encodeURIComponent(domain)}/pairing-start`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getChannelPairing(sessionId: string): Promise<ChannelPairingSessionPublic> {
  return request(`/api/v1/settings/channels/pairing/${encodeURIComponent(sessionId)}`);
}

export function getActiveChannelPairing(domain: ChannelDomain): Promise<ChannelPairingSessionPublic | null> {
  return request(`/api/v1/settings/channels/${encodeURIComponent(domain)}/pairing/active`);
}

export function listMailProviders(signal?: AbortSignal): Promise<{ providers: MailboxProviderInfo[] }> {
  return request("/api/v1/mail/providers", { signal });
}

export function mailProviderStatus(provider: string): Promise<Record<string, unknown>> {
  return request(`/api/v1/mail/providers/${provider}/status`, { method: "POST" });
}

export function mailProviderSetupAction(
  provider: string,
  action: string,
  payload: Record<string, unknown> = {},
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  return request(`/api/v1/mail/providers/${provider}/setup-action`, {
    method: "POST",
    body: JSON.stringify({ action, payload }),
    signal,
  });
}

export function mailTestSendPrepare(payload: {
  recipients: string[];
  subject: string;
  content?: string;
  content_html?: string;
  config_id?: number | null;
}): Promise<Record<string, unknown>> {
  return request("/api/v1/mail/test-send/prepare", { method: "POST", body: JSON.stringify(payload) });
}

export function mailTestSendConfirm(payload: MailboxSendConfirmRequest): Promise<Record<string, unknown>> {
  return request("/api/v1/mail/test-send/confirm", { method: "POST", body: JSON.stringify(payload) });
}

export function getGeneralSettings(): Promise<Record<string, unknown>> {
  return request("/api/v1/settings/general");
}

export function saveGeneralSettings(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  return request("/api/v1/settings/general", { method: "PUT", body: JSON.stringify(payload) });
}

export function exportPreview(): Promise<{ ok: boolean; session_count: number }> {
  return request("/api/v1/settings/data/export/preview", { method: "POST" });
}

export function exportStart(): Promise<{ ok: boolean; task_id: string; total: number }> {
  return request("/api/v1/settings/data/export/start", { method: "POST" });
}

export function exportProgress(taskId: string): Promise<{ progress: number; total: number; status: string; error?: string }> {
  return request(`/api/v1/settings/data/export/progress/${taskId}`);
}

export async function exportDownload(taskId: string): Promise<void> {
  const a = document.createElement("a");
  a.href = `/api/v1/settings/data/export/download/${taskId}`;
  a.download = `qa-agent-backup-${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export async function importData(file: File): Promise<Record<string, unknown>> {
  const form = new FormData();
  form.append("file", file);
  const resp = await fetch("/api/v1/settings/data/import", { method: "POST", body: form });
  if (!resp.ok) throw new Error(await readErrorMessage(resp));
  return resp.json();
}

export function cleanupData(payload: {
  action: string;
  dry_run: boolean;
  time_range_days?: number | null;
  confirm?: boolean;
}): Promise<Record<string, unknown>> {
  return request("/api/v1/settings/data/cleanup", { method: "POST", body: JSON.stringify(payload) });
}
