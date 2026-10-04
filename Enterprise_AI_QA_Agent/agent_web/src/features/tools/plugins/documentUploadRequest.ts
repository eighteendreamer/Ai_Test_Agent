import { t } from "../../../services/i18n";

export interface PendingDocumentRequest {
  request_id: string;
  signature: string;
  operation: "upload" | "version" | "initialize";
  project_id: string | null;
  filename?: string;
  title?: string | null;
  project_url?: string | null;
  expected_parent_version_id?: string;
  expected_catalog_revision?: number;
}

// Like the existing planning request recovery, retain identities, never file bytes.
export function readDocumentRequest(key: string): PendingDocumentRequest | null {
  const raw = sessionStorage.getItem(key);
  if (!raw) return null;
  const value = JSON.parse(raw) as PendingDocumentRequest;
  if (!value.request_id || !value.signature || !["upload", "version", "initialize"].includes(value.operation)) {
    throw new Error(t("apiDocs.pending_invalid"));
  }
  return value;
}

export async function prepareDocumentRequest(key: string, payload: object,
  fields: Omit<PendingDocumentRequest, "request_id" | "signature">): Promise<PendingDocumentRequest> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(payload)));
  const signature = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  const previous = readDocumentRequest(key);
  if (previous) {
    if (previous.signature !== signature) throw new Error(t("apiDocs.pending_changed"));
    return previous;
  }
  const next = { ...fields, signature, request_id: crypto.randomUUID() };
  sessionStorage.setItem(key, JSON.stringify(next));
  return next;
}

export function documentFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",")[1] : result);
    };
    reader.onerror = () => reject(reader.error || new Error(t("apiDocs.read_file_failed")));
    reader.readAsDataURL(file);
  });
}
