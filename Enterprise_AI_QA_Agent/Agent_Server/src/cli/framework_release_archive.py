"""Export/verify/restore a trusted local source release without service startup."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from src.runtime.framework_release_archive import archive_release, require_installed_release, restore_release, verify_release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="Archive committed code; imports that trusted code in an isolated probe")
    export.add_argument("--project", type=Path, required=True)
    export.add_argument("--store", type=Path, required=True)
    export.add_argument("--revision", default="HEAD")
    for name in ("verify", "restore", "verify-installed"):
        command = commands.add_parser(name)
        command.add_argument("--store", type=Path, required=True)
        command.add_argument("--reference", type=Path, required=True, help="JSON release reference, not credentials")
        if name == "restore":
            command.add_argument("--destination", type=Path, required=True)
        if name == "verify-installed":
            command.add_argument("--project", type=Path, required=True,
                help="Trusted installed source; check it before starting application services")
    args = parser.parse_args()
    try:
        if args.command == "export":
            result = archive_release(args.project, args.store, revision=args.revision).model_dump(mode="json")
        else:
            reference = json.loads(args.reference.read_text(encoding="utf-8-sig"))
            if args.command == "verify-installed":
                manifest = require_installed_release(args.project, args.store, reference)
            else:
                manifest = (restore_release(args.store, reference, args.destination) if args.command == "restore"
                    else verify_release(args.store, reference))
            result = {"source_revision": manifest.source_revision, "archive_sha256": reference["archive_sha256"],
                "file_count": len(manifest.files), "python_version": manifest.environment.get("python_version")}
            if args.command == "verify-installed":
                result["installed_source_verified"] = True
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        # An untrusted reference/manifest can contain private validation input.
        # Keep a searchable operation/type without dumping it in a traceback.
        print(f"framework_release_archive_failed operation={args.command} error_type={type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
