import type {
  DockerContainerActionRequest,
  DockerContainerActionResponse,
  DockerContainerCreateRequest,
  DockerContainerCreateResponse,
  DockerContainerLogsResponse,
  DockerContainerRemoveRequest,
  DockerImagePullRequest,
  DockerImagePullResponse,
  DockerImageRemoveRequest,
  DockerOverviewResponse,
  DockerTemplateCreateRequest,
} from "../types";
import { request } from "./http";

export function dockerOverview(): Promise<DockerOverviewResponse> {
  return request("/api/v1/docker/overview");
}

export function dockerPullImage(payload: DockerImagePullRequest): Promise<DockerImagePullResponse> {
  return request("/api/v1/docker/images/pull", { method: "POST", body: JSON.stringify(payload) });
}

export function dockerRemoveImage(
  payload: DockerImageRemoveRequest,
): Promise<{ ok: boolean; action: string; image: string }> {
  return request("/api/v1/docker/images/remove", { method: "POST", body: JSON.stringify(payload) });
}

export function dockerCreateContainer(payload: DockerContainerCreateRequest): Promise<DockerContainerCreateResponse> {
  return request("/api/v1/docker/containers", { method: "POST", body: JSON.stringify(payload) });
}

export function dockerCreateTemplateContainer(
  templateKey: string,
  payload: DockerTemplateCreateRequest,
): Promise<DockerContainerCreateResponse> {
  return request(`/api/v1/docker/templates/${encodeURIComponent(templateKey)}/create`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function dockerContainerAction(
  containerId: string,
  payload: DockerContainerActionRequest,
): Promise<DockerContainerActionResponse> {
  return request(`/api/v1/docker/containers/${encodeURIComponent(containerId)}/action`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function dockerRemoveContainer(
  containerId: string,
  payload: DockerContainerRemoveRequest,
): Promise<{ ok: boolean; action: string; container_id: string }> {
  return request(`/api/v1/docker/containers/${encodeURIComponent(containerId)}/remove`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function dockerContainerLogs(containerId: string, tail = 200): Promise<DockerContainerLogsResponse> {
  const params = new URLSearchParams({ tail: String(tail) });
  return request(`/api/v1/docker/containers/${encodeURIComponent(containerId)}/logs?${params.toString()}`);
}
