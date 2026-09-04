import type { ToolApprovalRequest } from "../types";
import { api } from "../services/api";

export class ApprovalManager {
  private _resolvingIds: string[] = [];

  get resolvingIds(): string[] {
    return this._resolvingIds;
  }

  set resolvingIds(value: string[]) {
    this._resolvingIds = value;
  }

  isResolving(approvalId: string): boolean {
    return this._resolvingIds.includes(approvalId);
  }

  async resolve(
    sessionId: string,
    approvalId: string,
    decision: "approved" | "denied",
    reason?: string,
  ): Promise<ToolApprovalRequest> {
    if (this._resolvingIds.includes(approvalId)) {
      throw new Error("该审批正在处理中，请勿重复点击。");
    }

    this._resolvingIds = [...this._resolvingIds, approvalId];
    try {
      return await api.resolveApproval(sessionId, approvalId, decision, reason);
    } finally {
      this._resolvingIds = this._resolvingIds.filter((id) => id !== approvalId);
    }
  }

  reset(): void {
    this._resolvingIds = [];
  }
}
