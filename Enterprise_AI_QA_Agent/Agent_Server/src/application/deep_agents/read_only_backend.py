from __future__ import annotations

from pathlib import Path
from typing import Any


def build_read_only_filesystem_backend(
    root: Path,
    *,
    max_file_size_mb: int,
    max_output_chars: int = 120000,
) -> Any:
    """Build the official filesystem backend with bounded text reads.

    No upstream counterpart (checked: local Deep Agents 0.7.13 backend API and
    project-cached Deep Agents backend docs): ``max_file_size_mb`` is applied by
    the upstream Python grep fallback, but ripgrep and ``read`` can still load
    an oversized file.  The subclass below only closes that safety gap; path
    resolution, virtual-root checks and the BackendProtocol remain upstream.
    """
    from deepagents.backends import FilesystemBackend
    from deepagents.backends.protocol import ReadResult

    max_bytes = max(1, int(max_file_size_mb)) * 1024 * 1024
    bounded_output_chars = max(1000, int(max_output_chars))

    class BoundedFilesystemBackend(FilesystemBackend):
        def _ripgrep_search(self, pattern, base_path, glob, max_count):
            # Force the upstream bounded Python search instead of an unbounded
            # ripgrep result.  Its implementation skips files over the same
            # max_file_size_bytes threshold.
            return None, False

        def read(self, file_path: str, offset: int = 0, limit: int = 2000):
            try:
                resolved = self._resolve_path(file_path)
                if resolved.is_file() and resolved.stat().st_size > max_bytes:
                    return ReadResult(
                        error=(
                            f"File '{file_path}' exceeds the DA-E2 read limit "
                            f"of {max_bytes} bytes."
                        )
                    )
                if resolved.is_file():
                    sample = resolved.read_bytes()[: min(max_bytes, 65536)]
                    if b"\x00" in sample:
                        return ReadResult(
                            error=f"Binary file reads are not enabled for '{file_path}'."
                        )
                    try:
                        sample.decode("utf-8")
                    except UnicodeDecodeError:
                        return ReadResult(
                            error=f"Binary file reads are not enabled for '{file_path}'."
                        )
            except (OSError, RuntimeError, ValueError) as exc:
                return ReadResult(error=f"Error reading file '{file_path}': {exc}")
            result = super().read(file_path, offset, limit)
            if (
                result.error is None
                and result.file_data is not None
                and result.file_data.get("encoding") == "utf-8"
                and len(result.file_data.get("content") or "") > bounded_output_chars
            ):
                return ReadResult(
                    error=(
                        f"Read window for '{file_path}' exceeds the DA-E2 output limit "
                        f"of {bounded_output_chars} characters; narrow the line range "
                        "or locate content with grep first."
                    )
                )
            return result

    return BoundedFilesystemBackend(
        root_dir=root,
        virtual_mode=True,
        max_file_size_mb=max(1, int(max_file_size_mb)),
    )
