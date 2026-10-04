"""Read exact local Git objects; never checkout, fetch, filter or mutate a repository."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

from src.schemas.document_git import GitDocumentPolicy, GitDocumentResolveRequest


class GitReader:
    def __init__(self, root: str, policy: GitDocumentPolicy):
        path = Path(root)
        if not path.is_absolute():
            raise ValueError("Git 来源必须登记服务器本地绝对仓库路径")
        self.root = path.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Git 仓库路径不是目录")
        self.policy = policy
        self.deadline = time.monotonic() + policy.timeout_seconds
        self.commands = []
        # Do not inherit caller Git overrides, tracing or alternate worktrees.
        self.env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")

    def run(self, *arguments, binary=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Git 读取超过时间限制")
        # Official git(1) flags: literal paths, immutable objects and local-only reads.
        command = ["git", "--no-replace-objects", "--no-lazy-fetch", "--no-optional-locks", "--literal-pathspecs", "-C", str(self.root), *arguments]
        try:
            result = subprocess.run(command, env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=remaining, check=False)
        except subprocess.TimeoutExpired as exc:
            partial = exc.stdout or b""
            diagnostic = {"command": list(arguments), "timeout": True,
                "stderr": (exc.stderr or b"").decode("utf-8", errors="replace")}
            if binary:
                diagnostic.update(stdout_size_bytes=len(partial), stdout_sha256=hashlib.sha256(partial).hexdigest())
            else:
                diagnostic["stdout"] = partial.decode("utf-8", errors="replace")
            print(json.dumps(diagnostic, ensure_ascii=False), file=sys.stderr)
            raise ValueError("Git 读取超过时间限制") from exc
        diagnostic = {"command": list(arguments), "exit_code": result.returncode,
            "stderr": result.stderr.decode("utf-8", errors="replace")}
        if binary:
            diagnostic.update(stdout_size_bytes=len(result.stdout), stdout_sha256=hashlib.sha256(result.stdout).hexdigest())
        else:
            diagnostic["stdout"] = result.stdout.decode("utf-8", errors="replace")
        self.commands.append(diagnostic)
        if result.returncode != 0:
            print(json.dumps(diagnostic, ensure_ascii=False), file=sys.stderr)
            raise ValueError(f"Git {arguments[0]} 读取失败（退出码 {result.returncode}）")
        if not binary and len(result.stdout) > 65536:
            raise ValueError("Git 元数据输出超过大小限制")
        return result.stdout

    def validate(self):
        version = self.run("--version").decode().strip()
        bare = self.run("rev-parse", "--is-bare-repository").decode().strip() == "true"
        actual = self.run("rev-parse", "--absolute-git-dir" if bare else "--show-toplevel").decode("utf-8").strip()
        if Path(actual).resolve() != self.root:
            raise ValueError("登记路径必须是准确的 Git 仓库根目录，不能自动向上查找")
        return {"repository_root": str(self.root), "git_version": version, "bare": bare}

    def file(self, reference: str, path: str, *, read=False, expected_blob=None):
        query = GitDocumentResolveRequest(repository_id="internal", reference=reference, path=path)
        identity = self.validate()
        commit = self.run("rev-parse", "--verify", "--end-of-options", query.reference + "^{commit}").decode().strip()
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
            raise ValueError("Git 未返回完整 commit 身份")
        entries = self.run("ls-tree", "--full-tree", "-z", commit, "--", query.path).split(b"\0")
        matches = [entry for entry in entries if entry and entry.split(b"\t", 1)[-1] == query.path.encode("utf-8")]
        if len(matches) != 1:
            raise ValueError("指定 commit 中没有该精确文档路径")
        header, _ = matches[0].split(b"\t", 1)
        mode, kind, blob = header.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Git 文档必须是普通文件，不支持目录、符号链接或子模块")
        if expected_blob is not None and blob != expected_blob:
            raise ValueError("Git blob 与已选择的固定来源不一致")
        size = int(self.run("cat-file", "-s", blob).decode().strip())
        if size > self.policy.max_blob_bytes:
            raise ValueError("Git 原件超过文件大小限制")
        raw = self.run("cat-file", "blob", blob, binary=True)
        if len(raw) != size:
            raise ValueError("Git 原件字节大小与 blob 元数据不一致")
        # Check the content-addressed object independently, including SHA-256 repositories.
        digest = hashlib.sha1() if len(blob) == 40 else hashlib.sha256()
        digest.update(f"blob {len(raw)}\0".encode("ascii")); digest.update(raw)
        if digest.hexdigest() != blob:
            raise ValueError("Git 原件字节与 blob 身份不一致")
        if raw.startswith(b"version https://git-lfs.github.com/spec/v1\n") or raw.startswith(b"version https://git-lfs.github.com/spec/v1\r\n"):
            raise ValueError("首期不读取 Git LFS 大文件，请上传实际原件")
        result = {**identity, "commit": commit, "blob": blob, "path": query.path, "mode": mode,
            "sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "commands": self.commands}
        if read:
            result["content_base64"] = base64.b64encode(raw).decode("ascii")
        return result


def main():
    input_path, output_path = map(Path, sys.argv[1:])
    try:
        request = json.loads(input_path.read_text(encoding="utf-8"))
        reader = GitReader(request["root_path"], GitDocumentPolicy.model_validate(request["policy"]))
        operation = request["operation"]
        if operation == "validate":
            result = {**reader.validate(), "commands": reader.commands}
        elif operation in {"resolve", "read"}:
            result = reader.file(request["reference"], request["path"], read=operation == "read", expected_blob=request.get("expected_blob"))
        else:
            raise ValueError("无效的内部 Git 操作")
        output_path.write_text(json.dumps({"ok": True, "result": result}, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        output_path.write_text(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), encoding="utf-8")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
