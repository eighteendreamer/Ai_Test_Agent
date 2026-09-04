import * as sessions from "./sessions.api";
import * as projects from "./projects.api";
import * as models from "./models.api";
import * as mcp from "./mcp.api";
import * as tools from "./tools.api";
import * as settings from "./settings.api";
import * as docker from "./docker.api";
import * as compatibility from "./compatibility.api";
import * as system from "./system.api";

export const api = {
  ...sessions,
  ...projects,
  ...models,
  ...mcp,
  ...tools,
  ...settings,
  ...docker,
  ...compatibility,
  ...system,
};
