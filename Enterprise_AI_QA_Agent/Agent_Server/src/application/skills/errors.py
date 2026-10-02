"""Source-specific failures shared by skill management and its API boundary."""


class SkillPathValidationError(ValueError):
    """An explicitly checked skill key or managed filesystem target is invalid."""
