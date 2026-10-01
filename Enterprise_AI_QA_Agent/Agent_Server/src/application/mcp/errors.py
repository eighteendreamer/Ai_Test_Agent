"""Explicit failures shared by MCP persistence, management and API adapters."""


class MCPServerNotFoundError(KeyError):
    """A successfully read catalog contains no requested server."""


class MCPManagementValidationError(ValueError):
    """An explicitly checked MCP management input is invalid."""


class MCPManagementUnavailableError(RuntimeError):
    """A required runtime component has not been configured."""


class MCPCatalogReadError(RuntimeError):
    """The compatibility catalog cannot be read or decoded."""
