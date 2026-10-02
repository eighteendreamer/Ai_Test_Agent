"""Source-specific failures shared by skill management and its API boundary."""


class SkillManagementValidationError(ValueError):
    """An explicitly checked skill input or user-supplied package is invalid."""


class SkillPathValidationError(SkillManagementValidationError):
    """An explicitly checked skill key or managed filesystem target is invalid."""


class SkillDownloadError(RuntimeError):
    """A skill download failed or a marketplace response is malformed."""
