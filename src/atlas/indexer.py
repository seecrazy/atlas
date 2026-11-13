"""File scanning, symbol extraction, and SQLite indexing."""

from __future__ import annotations

import ast
import hashlib
import pathlib
import re
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import replace

from .config import ProjectConfig
from .db import audit, connect, utcnow
from .store import rebuild_search_index
from .tree_backend import extract_with_tree_sitter, tree_sitter_available


LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
}

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".atlas",
    ".idea",
    ".vscode",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "target",
    ".venv",
    "venv",
}


@dataclass(frozen=True)
class Symbol:
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    exported: bool
    parent_qualified: str | None = None


@dataclass(frozen=True)
class FileRecord:
    path: str
    language: str
    module_name: str
    content_hash: str
    mtime: float
    symbols: tuple[Symbol, ...]
    imports: tuple[dict, ...]
    diagnostics: tuple[dict, ...] = ()


def is_excluded(path: pathlib.Path, project_config: ProjectConfig) -> bool:
    parts = set(path.parts)
    if any(part in EXCLUDED_DIRS for part in path.parts):
        return True
    for pattern in project_config.exclude:
        if pattern in parts or path.match(pattern):
            return True
    return False


def module_name_from_path(path: pathlib.Path) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or path.stem


def content_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _diagnostic(level: str, code: str, message: str) -> tuple[dict, ...]:
    return ({"level": level, "code": code, "message": message},)


def extract_python(path: pathlib.Path, module_name: str) -> tuple[FileRecord, bytes]:
    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
        tree = ast.parse(text, filename=str(path))
    except UnicodeDecodeError as exc:
        return FileRecord(
            path=path.as_posix(),
            language="python",
            module_name=module_name,
            content_hash=content_digest(content),
            mtime=path.stat().st_mtime,
            symbols=(),
            imports=(),
            diagnostics=_diagnostic("error", "decode_error", f"file is not valid UTF-8: {exc}"),
        ), content
    except SyntaxError as exc:
        return FileRecord(
            path=path.as_posix(),
            language="python",
            module_name=module_name,
            content_hash=content_digest(content),
            mtime=path.stat().st_mtime,
            symbols=(),
            imports=(),
            diagnostics=_diagnostic("error", "python_syntax_error", str(exc)),
        ), content

    symbols: list[Symbol] = []
    imports: list[dict] = []

    def add_symbol(
        node: ast.AST,
        name: str,
        kind: str,
        parent: str | None,
        exported: bool,
    ) -> None:
        qualified = f"{parent}.{name}" if parent else f"{module_name}.{name}"
        symbols.append(
            Symbol(
                name=name,
                qualified_name=qualified,
                kind=kind,
                start_line=getattr(node, "lineno", 1),
                end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                exported=exported,
                parent_qualified=parent,
            )
        )

    def walk(node: ast.AST, parent: str | None = None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.Import, ast.ImportFrom)):
                line = getattr(child, "lineno", 1)
                if isinstance(child, ast.Import):
                    for alias in child.names:
                        imports.append({"target": alias.name, "line": line, "kind": "import"})
                else:
                    base = child.module or ""
                    for alias in child.names:
                        target = f"{base}.{alias.name}" if base else alias.name
                        imports.append({"target": target, "line": line, "kind": "import"})
                continue
            if isinstance(child, ast.Call):
                function = child.func
                if isinstance(function, ast.Name):
                    target = function.id
                elif isinstance(function, ast.Attribute):
                    target = function.attr
                else:
                    target = None
                if target:
                    imports.append({
                        "target": target,
                        "line": getattr(child, "lineno", 1),
                        "kind": "call",
                    })
                walk(child, parent)
                continue
            if isinstance(child, ast.ClassDef):
                add_symbol(child, child.name, "class", parent, parent is None)
                walk(child, f"{parent}.{child.name}" if parent else f"{module_name}.{child.name}")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if parent else "function"
                add_symbol(child, child.name, kind, parent, parent is None)
                walk(child, f"{parent}.{child.name}" if parent else f"{module_name}.{child.name}")
            else:
                walk(child, parent)

    walk(tree)
    return FileRecord(
        path=path.as_posix(),
        language="python",
        module_name=module_name,
        content_hash=content_digest(content),
        mtime=path.stat().st_mtime,
        symbols=tuple(symbols),
        imports=tuple(imports),
    ), content


TS_CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
TS_FUNCTION_RE = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
TS_ASSIGN_RE = re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:function|[\w$]+(?:\s*=>)|\()", re.MULTILINE)
TS_EXPORT_RE = re.compile(r"^\s*export\s+(?:type|interface|enum)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)
TS_IMPORT_RE = re.compile(r"^\s*import\s+(?:[\w$*{},\s]+?\s+from\s+)?['\"]([^'\"]+)['\"]", re.MULTILINE)
TS_CALL_RE = re.compile(r"(?<!\w)([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)
TS_CALL_EXCLUDE = {
    "if", "for", "while", "switch", "catch", "function", "return", "new",
    "class", "constructor", "super", "typeof",
}


def extract_ts_js(path: pathlib.Path, module_name: str) -> tuple[FileRecord, bytes]:
    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return FileRecord(
            path=path.as_posix(),
            language=LANGUAGE_EXTENSIONS[path.suffix],
            module_name=module_name,
            content_hash=content_digest(content),
            mtime=path.stat().st_mtime,
            symbols=(),
            imports=(),
            diagnostics=_diagnostic("error", "decode_error", "file is not valid UTF-8"),
        ), content

    symbols: list[Symbol] = []
    imports: list[dict] = [
        {"target": target, "line": line_no, "kind": "import"}
        for line_no, target in enumerate(
            (match.group(1) for match in TS_IMPORT_RE.finditer(text)),
            start=1,
        )
    ]

    for match in TS_CLASS_RE.finditer(text):
        symbols.append(
            Symbol(
                name=match.group(1),
                qualified_name=f"{module_name}.{match.group(1)}",
                kind="class",
                start_line=text.count("\n", 0, match.start()) + 1,
                end_line=text.count("\n", 0, match.start()) + 1,
                exported=match.group(0).lstrip().startswith("export"),
            )
        )
    for match in TS_FUNCTION_RE.finditer(text):
        symbols.append(
            Symbol(
                name=match.group(1),
                qualified_name=f"{module_name}.{match.group(1)}",
                kind="function",
                start_line=text.count("\n", 0, match.start()) + 1,
                end_line=text.count("\n", 0, match.start()) + 1,
                exported=match.group(0).lstrip().startswith("export"),
            )
        )
    for match in TS_CALL_RE.finditer(text):
        target = match.group(1)
        if target in TS_CALL_EXCLUDE:
            continue
        imports.append({
            "target": target,
            "line": text.count("\n", 0, match.start()) + 1,
            "kind": "call",
        })
    for regex, kind in (
        (TS_ASSIGN_RE, "variable"),
        (TS_EXPORT_RE, "type"),
    ):
        for match in regex.finditer(text):
            symbols.append(
                Symbol(
                    name=match.group(1),
                    qualified_name=f"{module_name}.{match.group(1)}",
                    kind=kind,
                    start_line=text.count("\n", 0, match.start()) + 1,
                    end_line=text.count("\n", 0, match.start()) + 1,
                    exported=match.group(0).lstrip().startswith("export"),
                )
            )

    language = LANGUAGE_EXTENSIONS[path.suffix]
    return FileRecord(
        path=path.as_posix(),
        language=language,
        module_name=module_name,
        content_hash=content_digest(content),
        mtime=path.stat().st_mtime,
        symbols=tuple(symbols),
        imports=tuple(imports),
    ), content


GO_PACKAGE_RE = re.compile(r"^\s*package\s+([A-Za-z_][\w]*)", re.MULTILINE)
GO_SINGLE_IMPORT_RE = re.compile(r"^\s*import\s+(?:[_\.]\s+)?\"([^\"]+)\"", re.MULTILINE)
GO_BLOCK_IMPORT_RE = re.compile(r"^\s*import\s*\(([^)]*)\)", re.MULTILINE | re.DOTALL)
GO_TYPE_RE = re.compile(r"^\s*type\s+([A-Za-z_][\w]*)\s+(?:struct|interface)\b", re.MULTILINE)
GO_METHOD_RE = re.compile(
    r"^\s*func\s*\(\s*\w+\s+\*?([A-Za-z_][\w]*)\s*\)\s*([A-Za-z_][\w]*)\s*\(",
    re.MULTILINE,
)
GO_FUNCTION_RE = re.compile(r"^\s*func\s+([A-Za-z_][\w]*)\s*\(", re.MULTILINE)
GO_VALUE_RE = re.compile(r"^\s*(?:var|const)\s+([A-Za-z_][\w]*)\s*(?:=|\b)", re.MULTILINE)
GO_CALL_RE = re.compile(r"(?<!func\s)(?<!\w)([A-Za-z_][\w.]*)\s*\(", re.MULTILINE)


def extract_go(path: pathlib.Path, module_name: str) -> tuple[FileRecord, bytes]:
    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return FileRecord(
            path=path.as_posix(),
            language="go",
            module_name=module_name,
            content_hash=content_digest(content),
            mtime=path.stat().st_mtime,
            symbols=(),
            imports=(),
            diagnostics=_diagnostic("error", "decode_error", "file is not valid UTF-8"),
        ), content

    package_match = GO_PACKAGE_RE.search(text)
    package = package_match.group(1) if package_match else module_name.replace("/", ".")
    imports: list[dict] = []
    for match in GO_SINGLE_IMPORT_RE.finditer(text):
        imports.append(
            {
                "target": match.group(1),
                "line": text.count("\n", 0, match.start()) + 1,
                "kind": "import",
            }
        )
    for block in GO_BLOCK_IMPORT_RE.finditer(text):
        block_start = block.start()
        for quoted in re.finditer(r"\"([^\"]+)\"", block.group(1)):
            imports.append(
                {
                    "target": quoted.group(1),
                    "line": text.count("\n", 0, block_start + quoted.start()) + 1,
                    "kind": "import",
                }
            )

    symbols: list[Symbol] = []
    for match in GO_TYPE_RE.finditer(text):
        name = match.group(1)
        symbols.append(
            Symbol(
                name=name,
                qualified_name=f"{package}.{name}",
                kind="type",
                start_line=text.count("\n", 0, match.start()) + 1,
                end_line=text.count("\n", 0, match.start()) + 1,
                exported=name[0].isupper(),
            )
        )
    for regex, kind in ((GO_FUNCTION_RE, "function"), (GO_VALUE_RE, "variable")):
        for match in regex.finditer(text):
            name = match.group(1)
            symbols.append(
                Symbol(
                    name=name,
                    qualified_name=f"{package}.{name}",
                    kind=kind,
                    start_line=text.count("\n", 0, match.start()) + 1,
                    end_line=text.count("\n", 0, match.start()) + 1,
                    exported=name[0].isupper(),
                )
            )
    for match in GO_METHOD_RE.finditer(text):
        receiver = match.group(1)
        name = match.group(2)
        parent = f"{package}.{receiver}"
        symbols.append(
            Symbol(
                name=name,
                qualified_name=f"{parent}.{name}",
                kind="method",
                start_line=text.count("\n", 0, match.start()) + 1,
                end_line=text.count("\n", 0, match.start()) + 1,
                exported=name[0].isupper(),
                parent_qualified=parent,
            )
        )
    for match in GO_CALL_RE.finditer(text):
        target = match.group(1)
        if target in {"if", "for", "switch", "func", "make", "new"}:
            continue
        imports.append({
            "target": target,
            "line": text.count("\n", 0, match.start()) + 1,
            "kind": "call",
        })

    return FileRecord(
        path=path.as_posix(),
        language="go",
        module_name=package,
        content_hash=content_digest(content),
        mtime=path.stat().st_mtime,
        symbols=tuple(symbols),
        imports=tuple(imports),
    ), content


JAVA_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
JAVA_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([\w.*]+)\s*;", re.MULTILINE)
JAVA_TYPE_RE = re.compile(
    r"^\s*((?:public|protected|private|final|abstract|sealed)\s+)*"
    r"(class|interface|enum)\s+([A-Za-z_$][\w$]*)",
    re.MULTILINE,
)
JAVA_METHOD_RE = re.compile(
    r"^\s*((?:public|protected|private|static|final|abstract|synchronized|native)\s+)*"
    r"(?:<[^>]+>\s*)?[\w<>\[\], .?]+\s+([A-Za-z_$][\w$]*)\s*\(",
    re.MULTILINE,
)
JAVA_CALL_RE = re.compile(r"(?<!\w)([A-Za-z_$][\w$]*)\s*\(", re.MULTILINE)


def extract_java(path: pathlib.Path, module_name: str) -> tuple[FileRecord, bytes]:
    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return FileRecord(
            path=path.as_posix(),
            language="java",
            module_name=module_name,
            content_hash=content_digest(content),
            mtime=path.stat().st_mtime,
            symbols=(),
            imports=(),
            diagnostics=_diagnostic("error", "decode_error", "file is not valid UTF-8"),
        ), content

    package_match = JAVA_PACKAGE_RE.search(text)
    package = package_match.group(1) if package_match else module_name.replace("/", ".")
    imports = [
        {
            "target": match.group(1),
            "line": text.count("\n", 0, match.start()) + 1,
            "kind": "import",
        }
        for match in JAVA_IMPORT_RE.finditer(text)
    ]

    types: list[Symbol] = []
    for match in JAVA_TYPE_RE.finditer(text):
        name = match.group(3)
        types.append(
            Symbol(
                name=name,
                qualified_name=f"{package}.{name}",
                kind=match.group(2),
                start_line=text.count("\n", 0, match.start()) + 1,
                end_line=text.count("\n", 0, match.start()) + 1,
                exported="public" in (match.group(1) or ""),
            )
        )

    symbols = list(types)
    for match in JAVA_METHOD_RE.finditer(text):
        name = match.group(2)
        if name in {"if", "for", "while", "switch", "catch", "return", "new", "synchronized"}:
            continue
        line = text.count("\n", 0, match.start()) + 1
        parent = None
        for symbol in reversed(types):
            if symbol.start_line <= line:
                parent = symbol.qualified_name
                break
        symbols.append(
            Symbol(
                name=name,
                qualified_name=f"{parent}.{name}" if parent else f"{package}.{name}",
                kind="method",
                start_line=line,
                end_line=line,
                exported="public" in (match.group(1) or ""),
                parent_qualified=parent,
            )
        )
    for match in JAVA_CALL_RE.finditer(text):
        target = match.group(1)
        if target in {"if", "for", "while", "switch", "catch", "return", "new", "synchronized"}:
            continue
        imports.append({
            "target": target,
            "line": text.count("\n", 0, match.start()) + 1,
            "kind": "call",
        })

    return FileRecord(
        path=path.as_posix(),
        language="java",
        module_name=package,
        content_hash=content_digest(content),
        mtime=path.stat().st_mtime,
        symbols=tuple(symbols),
        imports=tuple(imports),
    ), content


RUST_USE_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]+\))?\s+)?use\s+([A-Za-z_][\w:{}:, \s]*?)\s*;",
    re.MULTILINE,
)
RUST_TYPE_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]+\))?\s+)?(?:struct|enum|trait|type)\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)
RUST_FUNCTION_RE = re.compile(
    r"^\s*(?:pub(?:\([^)]+\))?\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+([A-Za-z_][\w]*)",
    re.MULTILINE,
)
RUST_IMPL_RE = re.compile(
    r"^\s*impl(?:<[^>]*>)?\s+(?:[\w:]+\s+for\s+)?([A-Za-z_][\w:]*)",
    re.MULTILINE,
)
RUST_CALL_RE = re.compile(r"(?<!fn\s)(?<!\w)([A-Za-z_][\w:]*)\s*\(", re.MULTILINE)


def extract_rust(path: pathlib.Path, module_name: str) -> tuple[FileRecord, bytes]:
    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return FileRecord(
            path=path.as_posix(),
            language="rust",
            module_name=module_name,
            content_hash=content_digest(content),
            mtime=path.stat().st_mtime,
            symbols=(),
            imports=(),
            diagnostics=_diagnostic("error", "decode_error", "file is not valid UTF-8"),
        ), content

    imports = []
    for match in RUST_USE_RE.finditer(text):
        target = re.sub(r"\s+", "", match.group(1))
        imports.append(
            {
                "target": target,
                "line": text.count("\n", 0, match.start()) + 1,
                "kind": "use",
            }
        )

    impl_markers = [
        (text.count("\n", 0, match.start()) + 1, match.group(1).split("::")[-1])
        for match in RUST_IMPL_RE.finditer(text)
    ]
    symbols: list[Symbol] = []
    for match in RUST_TYPE_RE.finditer(text):
        name = match.group(1)
        symbols.append(
            Symbol(
                name=name,
                qualified_name=f"{module_name}.{name}",
                kind="type",
                start_line=text.count("\n", 0, match.start()) + 1,
                end_line=text.count("\n", 0, match.start()) + 1,
                exported=match.group(0).lstrip().startswith("pub"),
            )
        )
    for match in RUST_FUNCTION_RE.finditer(text):
        name = match.group(1)
        line = text.count("\n", 0, match.start()) + 1
        parent = None
        for impl_line, impl_type in reversed(impl_markers):
            if impl_line <= line:
                parent = f"{module_name}.{impl_type}"
                break
        symbols.append(
            Symbol(
                name=name,
                qualified_name=f"{parent}.{name}" if parent else f"{module_name}.{name}",
                kind="method" if parent else "function",
                start_line=line,
                end_line=line,
                exported=match.group(0).lstrip().startswith("pub"),
                parent_qualified=parent,
            )
        )
    for match in RUST_CALL_RE.finditer(text):
        target = match.group(1)
        if target in {"if", "match", "while", "for", "Some", "Ok", "Err"}:
            continue
        imports.append({
            "target": target,
            "line": text.count("\n", 0, match.start()) + 1,
            "kind": "call",
        })

    return FileRecord(
        path=path.as_posix(),
        language="rust",
        module_name=module_name,
        content_hash=content_digest(content),
        mtime=path.stat().st_mtime,
        symbols=tuple(symbols),
        imports=tuple(imports),
    ), content


def scan_file(
    path: pathlib.Path,
    display_path: pathlib.Path | None = None,
    *,
    parser_backend: str = "auto",
) -> FileRecord | None:
    language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())
    if language not in {"python", "typescript", "javascript", "go", "java", "rust"}:
        return None
    display_path = display_path or path
    module_name = module_name_from_path(display_path)
    if parser_backend in {"auto", "tree-sitter"} and tree_sitter_available():
        ts_record = extract_with_tree_sitter(path, module_name, language)
        if ts_record is not None:
            return replace(ts_record, path=display_path.as_posix())
    if language == "python":
        record, _ = extract_python(path, module_name)
        return replace(record, path=display_path.as_posix())
    if language == "go":
        record, _ = extract_go(path, module_name)
        return replace(record, path=display_path.as_posix())
    if language == "java":
        record, _ = extract_java(path, module_name)
        return replace(record, path=display_path.as_posix())
    if language == "rust":
        record, _ = extract_rust(path, module_name)
        return replace(record, path=display_path.as_posix())
    record, _ = extract_ts_js(path, module_name)
    return replace(record, path=display_path.as_posix())


def scan_repo(repo_dir: pathlib.Path, project_config: ProjectConfig) -> list[FileRecord]:
    records: list[FileRecord] = []
    paths = [
        path for path in sorted(repo_dir.rglob("*"))
        if path.is_file() and not is_excluded(path, project_config)
    ]
    if not paths:
        return records
    max_workers = max(1, min(int(project_config.max_workers), len(paths)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for record in executor.map(
            lambda path: scan_file(
                path,
                path.relative_to(repo_dir),
                parser_backend=project_config.parser_backend,
            ),
            paths,
        ):
            if record is not None:
                records.append(record)
    return records


def upsert_repo(conn: sqlite3.Connection, subsystem: str, repo: str, repo_dir: pathlib.Path) -> int:
    name = repo_dir.name if repo == "." else repo
    conn.execute(
        "INSERT INTO repos(name, subsystem, path) VALUES(?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET subsystem=excluded.subsystem, path=excluded.path",
        (name, subsystem, str(repo_dir)),
    )
    row = conn.execute("SELECT id FROM repos WHERE name = ?", (name,)).fetchone()
    return int(row["id"])


def _run_git(repo_dir: pathlib.Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    # Only treat an explicitly configured repository as a Git unit. A nested
    # directory inside a parent monorepo falls back to full scanning because its
    # diff paths would otherwise include the monorepo prefix.
    if not (repo_dir / ".git").exists():
        return None
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def git_head_commit(repo_dir: pathlib.Path) -> str | None:
    result = _run_git(repo_dir, ["rev-parse", "HEAD"])
    commit = result.stdout.strip() if result is not None else ""
    return commit or None


def git_changed_files(
    repo_dir: pathlib.Path,
    old_commit: str,
    new_commit: str,
) -> dict[str, str] | None:
    """Return Git path -> change status between two commits.

    `diff-tree` emits NUL-separated records. Rename and copy records have an
    extra source path, so parsing must consume two paths for R/C statuses.
    """
    result = _run_git(
        repo_dir,
        ["diff-tree", "-r", "--name-status", "--no-commit-id", "-z", old_commit, new_commit],
    )
    if result is None:
        return None
    return _parse_name_status(result.stdout)


def _parse_name_status(output: str) -> dict[str, str]:
    """Parse Git -z name-status records with R/C path pairs."""
    parts = output.split("\0")
    changed: dict[str, str] = {}
    index = 0
    while index < len(parts):
        item = parts[index]
        if not item:
            index += 1
            continue
        status = item[:1]
        if status in {"R", "C"} and index + 2 < len(parts):
            changed[parts[index + 2]] = status
            if status == "R":
                changed[parts[index + 1]] = "D"
            index += 3
        else:
            path = parts[index + 1] if index + 1 < len(parts) else ""
            if path:
                changed[path] = status
            index += 2
    return changed


def git_workspace_changes(repo_dir: pathlib.Path, head_commit: str) -> dict[str, str] | None:
    """Return uncommitted tracked changes plus untracked supported candidates."""
    tracked_result = _run_git(
        repo_dir,
        ["diff-index", "--name-status", "--no-commit-id", "-z", "HEAD", "--"],
    )
    if tracked_result is None:
        return None
    changed = _parse_name_status(tracked_result.stdout)
    untracked_result = _run_git(
        repo_dir,
        ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    if untracked_result is not None:
        for path in untracked_result.stdout.split("\0"):
            if path:
                changed[path] = "A"
    return changed


def _last_indexed_commit(conn: sqlite3.Connection, repo_id: int) -> str | None:
    row = conn.execute(
        "SELECT last_commit FROM index_state WHERE repo_id = ?", (repo_id,)
    ).fetchone()
    return row["last_commit"] if row is not None else None


def _record_index_state(conn: sqlite3.Connection, repo_id: int, commit: str | None) -> None:
    if not commit:
        return
    conn.execute(
        """
        INSERT INTO index_state(repo_id, last_commit, last_indexed_at) VALUES(?,?,?)
        ON CONFLICT(repo_id) DO UPDATE SET
          last_commit=excluded.last_commit,
          last_indexed_at=excluded.last_indexed_at
        """,
        (repo_id, commit, utcnow()),
    )


def _insert_diagnostics(
    conn: sqlite3.Connection,
    repo_id: int,
    stable_path: str,
    diagnostics: tuple[dict, ...],
) -> int:
    if not diagnostics:
        return 0
    conn.execute(
        "DELETE FROM index_diagnostics WHERE repo_id = ? AND file_path = ?",
        (repo_id, stable_path),
    )
    for item in diagnostics:
        conn.execute(
            """
            INSERT INTO index_diagnostics(repo_id, file_path, level, code, message, created_at)
            VALUES(?,?,?,?,?,?)
            """,
            (repo_id, stable_path, item["level"], item["code"], item["message"], utcnow()),
        )
    return len(diagnostics)


def _file_changed(
    conn: sqlite3.Connection,
    repo_id: int,
    rel_path: str,
    record: FileRecord,
) -> bool:
    row = conn.execute(
        "SELECT content_hash FROM files WHERE repo_id = ? AND path = ?",
        (repo_id, rel_path),
    ).fetchone()
    return row is None or row["content_hash"] != record.content_hash


def _insert_file_record(conn: sqlite3.Connection, repo_id: int, record: FileRecord) -> int:
    conn.execute(
        """
        DELETE FROM symbols WHERE file_id = (
          SELECT id FROM files WHERE repo_id = ? AND path = ?
        )
        """,
        (repo_id, record.path),
    )
    conn.execute(
        "DELETE FROM refs WHERE source_file_id = (SELECT id FROM files WHERE repo_id = ? AND path = ?)",
        (repo_id, record.path),
    )
    conn.execute(
        "DELETE FROM memory_evidence WHERE file_id = (SELECT id FROM files WHERE repo_id = ? AND path = ?)",
        (repo_id, record.path),
    )
    conn.execute(
        "DELETE FROM files WHERE repo_id = ? AND path = ?",
        (repo_id, record.path),
    )
    cursor = conn.execute(
        """
        INSERT INTO files(repo_id, path, language, module_name, content_hash, mtime, status)
        VALUES(?,?,?,?,?,?,'valid')
        """,
        (repo_id, record.path, record.language, record.module_name, record.content_hash, record.mtime),
    )
    file_id = int(cursor.lastrowid)
    for symbol in record.symbols:
        conn.execute(
            """
            INSERT INTO symbols(
              file_id, repo_id, name, qualified_name, kind,
              parent_qualified, start_line, end_line, exported
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                file_id,
                repo_id,
                symbol.name,
                symbol.qualified_name,
                symbol.kind,
                symbol.parent_qualified,
                symbol.start_line,
                symbol.end_line,
                int(symbol.exported),
            ),
        )
    for imported in record.imports:
        conn.execute(
            "INSERT INTO refs(source_file_id, source_repo_id, target_name, line, kind) VALUES(?,?,?,?,?)",
            (file_id, repo_id, imported["target"], imported["line"], imported["kind"]),
        )
    return file_id


def mark_memory_drift(conn: sqlite3.Connection, file_ids: list[int]) -> int:
    if not file_ids:
        return 0
    placeholders = ",".join("?" for _ in file_ids)
    cursor = conn.execute(
        f"""
        UPDATE memories
           SET status = 'stale', updated_at = ?
         WHERE status = 'valid'
           AND id IN (
             SELECT memory_id FROM memory_evidence WHERE file_id IN ({placeholders})
           )
        """,
        (utcnow(), *file_ids),
    )
    return int(cursor.rowcount or 0)


def _resolve_references(conn: sqlite3.Connection) -> dict:
    """Bind import/use edges to indexed symbols when a mechanical match exists."""
    rows = conn.execute(
        "SELECT id, target_name, source_repo_id FROM refs ORDER BY id"
    ).fetchall()
    counts = {"resolved": 0, "unique_name": 0, "ambiguous": 0, "unresolved": 0}
    for row in rows:
        target = row["target_name"]
        symbol_rows = conn.execute(
            "SELECT id, qualified_name, repo_id FROM symbols WHERE qualified_name = ?",
            (target,),
        ).fetchall()
        if len(symbol_rows) == 1:
            status = "resolved"
            method = "qualified_name"
            confidence = 0.95
        else:
            if "/" in target:
                candidate = pathlib.PurePosixPath(target).name
            else:
                candidate = target.rsplit(".", 1)[-1]
            symbol_rows = conn.execute(
                "SELECT id, qualified_name, repo_id FROM symbols WHERE name = ? ORDER BY id",
                (candidate,),
            ).fetchall()
            if len(symbol_rows) == 1:
                status = "unique_name"
                method = "unique_name"
                confidence = 0.65
            elif len(symbol_rows) > 1:
                status = "ambiguous"
                method = "same_name_candidates"
                confidence = 0.45
            else:
                status = "unresolved"
                method = None
                confidence = 0.0

        symbol_id = symbol_rows[0]["id"] if len(symbol_rows) == 1 else None
        symbol_name = symbol_rows[0]["qualified_name"] if len(symbol_rows) == 1 else None
        conn.execute(
            """
            UPDATE refs
               SET target_symbol_id = ?, target_symbol = ?, resolution_status = ?,
                   resolution_method = ?, confidence = ?
             WHERE id = ?
            """,
            (symbol_id, symbol_name, status, method, confidence, row["id"]),
        )
        counts[status] += 1
    return counts


def _exported_symbols(
    conn: sqlite3.Connection,
    repo_id: int,
    stable_path: str,
) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT s.name, s.qualified_name
        FROM symbols s
        JOIN files f ON f.id = s.file_id
        WHERE f.repo_id = ? AND f.path = ? AND s.exported = 1
        ORDER BY s.qualified_name
        """,
        (repo_id, stable_path),
    ).fetchall()
    return {row["name"]: row["qualified_name"] for row in rows}


def _record_exported_changes(
    conn: sqlite3.Connection,
    repo_id: int,
    stable_path: str,
    old_symbols: dict[str, str],
    new_symbols: dict[str, str],
) -> int:
    changes = []
    for name, qualified_name in old_symbols.items():
        if name not in new_symbols:
            changes.append(("removed", name, qualified_name))
    for name, qualified_name in new_symbols.items():
        if name not in old_symbols:
            changes.append(("added", name, qualified_name))
    conn.executemany(
        """
        INSERT INTO exported_symbol_changes(
          repo_id, file_path, symbol, qualified_name, change_type, indexed_at
        ) VALUES(?,?,?,?,?,?)
        """,
        [
            (
                repo_id,
                stable_path,
                symbol,
                qualified_name,
                change_type,
                utcnow(),
            )
            for change_type, symbol, qualified_name in changes
        ],
    )
    return len(changes)


def index_project(config: ProjectConfig) -> dict:
    return index_project(config, full=False)


def index_project(
    config: ProjectConfig,
    *,
    full: bool = False,
    include_workspace: bool = True,
) -> dict:
    conn = connect(config.db_path, create=True)
    stats = {
        "repos": 0,
        "files_scanned": 0,
        "files_changed": 0,
        "files_unchanged": 0,
        "symbols": 0,
        "imports": 0,
        "calls": 0,
        "mode": "full",
        "git_candidates": 0,
        "workspace_candidates": 0,
        "exported_symbol_changes": 0,
        "diagnostics": 0,
        "memories_marked_stale": 0,
    }
    seen_repo_ids: set[int] = set()
    try:
        for subsystem in config.subsystems:
            for repo in subsystem["repos"]:
                repo_dir = (config.project_root / repo).resolve()
                repo_name = repo_dir.name if repo == "." else repo
                repo_id = upsert_repo(conn, subsystem["name"], repo, repo_dir)
                seen_repo_ids.add(repo_id)
                stats["repos"] += 1
                indexed_paths: set[str] = set()
                head_commit = git_head_commit(repo_dir)
                previous_commit = None if full else _last_indexed_commit(conn, repo_id)
                changed_paths: dict[str, str] = {}
                incremental = bool(head_commit and previous_commit)
                if incremental:
                    stats["mode"] = "git-diff-tree"
                if incremental and previous_commit != head_commit:
                    diff_result = git_changed_files(repo_dir, previous_commit, head_commit)
                    if diff_result is None:
                        incremental = False
                    else:
                        changed_paths = diff_result
                if incremental and include_workspace and head_commit:
                    workspace_result = git_workspace_changes(repo_dir, head_commit)
                    if workspace_result is None:
                        incremental = False
                    else:
                        changed_paths.update(workspace_result)
                        stats["mode"] = "git-worktree"
                        stats["workspace_candidates"] += len(workspace_result)
                if incremental:
                    stats["git_candidates"] += len(changed_paths)

                # Deletions are removed directly; they cannot be scanned as files.
                for rel_path, status in sorted(changed_paths.items()):
                    if status != "D":
                        continue
                    stable_path = f"{repo_name}/{rel_path}"
                    old_file_row = conn.execute(
                        "SELECT id FROM files WHERE repo_id = ? AND path = ?",
                        (repo_id, stable_path),
                    ).fetchone()
                    if old_file_row is not None:
                        stats["memories_marked_stale"] += mark_memory_drift(
                            conn, [int(old_file_row["id"])]
                        )
                        old_exports = _exported_symbols(conn, repo_id, stable_path)
                        stats["exported_symbol_changes"] += _record_exported_changes(
                            conn, repo_id, stable_path, old_exports, {}
                        )
                        conn.execute(
                            "DELETE FROM files WHERE repo_id = ? AND path = ?",
                            (repo_id, stable_path),
                        )

                candidate_records: list[FileRecord] = []
                if changed_paths:
                    supported = {
                        rel_path
                        for rel_path, status in changed_paths.items()
                        if status != "D"
                        and LANGUAGE_EXTENSIONS.get(pathlib.PurePath(rel_path).suffix.lower())
                        and not is_excluded(pathlib.Path(rel_path), config)
                    }
                    for rel_path in sorted(supported):
                        absolute_path = repo_dir / rel_path
                        if not absolute_path.is_file():
                            continue
                        record = scan_file(
                            absolute_path,
                            pathlib.Path(rel_path),
                            parser_backend=config.parser_backend,
                        )
                        if record is not None:
                            candidate_records.append(record)
                if not incremental:
                    candidate_records = scan_repo(repo_dir, config)

                conn.execute(
                    "DELETE FROM index_diagnostics WHERE repo_id = ?", (repo_id,)
                )
                for record in candidate_records:
                    rel_path = record.path
                    # Project-wide stable paths include the repo name. This makes
                    # scope boundaries and query output unambiguous across repos.
                    record = replace(record, path=f"{repo_name}/{rel_path}")
                    indexed_paths.add(rel_path)
                    indexed_paths.add(record.path)
                    stable_path = record.path
                    stats["files_scanned"] += 1
                    stats["symbols"] += len(record.symbols)
                    stats["imports"] += sum(1 for item in record.imports if item["kind"] == "import")
                    stats["calls"] += sum(1 for item in record.imports if item["kind"] == "call")
                    if not _file_changed(conn, repo_id, stable_path, record):
                        stats["files_unchanged"] += 1
                        continue
                    old_file_row = conn.execute(
                        "SELECT id FROM files WHERE repo_id = ? AND path = ?",
                        (repo_id, stable_path),
                    ).fetchone()
                    if old_file_row is not None:
                        stats["memories_marked_stale"] += mark_memory_drift(
                            conn, [int(old_file_row["id"])]
                        )
                    old_exports = _exported_symbols(conn, repo_id, stable_path)
                    file_id = _insert_file_record(conn, repo_id, record)
                    new_exports = _exported_symbols(conn, repo_id, stable_path)
                    stats["exported_symbol_changes"] += _record_exported_changes(
                        conn, repo_id, stable_path, old_exports, new_exports
                    )
                    stats["diagnostics"] += _insert_diagnostics(
                        conn, repo_id, stable_path, record.diagnostics
                    )
                    stats["files_changed"] += 1

                _record_index_state(conn, repo_id, head_commit)

                # Full scans reconcile deletions by comparing the complete path set.
                # Incremental scans already handled explicit D statuses; reconciling
                # against a partial candidate set would incorrectly delete unaffected
                # files.
                if not incremental:
                    existing_paths = {
                        row["path"]
                        for row in conn.execute(
                            "SELECT path FROM files WHERE repo_id = ?", (repo_id,)
                        )
                    }
                    for deleted_path in existing_paths - indexed_paths:
                        removed_exports = _exported_symbols(conn, repo_id, deleted_path)
                        stats["exported_symbol_changes"] += _record_exported_changes(
                            conn, repo_id, deleted_path, removed_exports, {}
                        )
                        conn.execute(
                            "DELETE FROM files WHERE repo_id = ? AND path = ?",
                            (repo_id, deleted_path),
                        )
        # Python sqlite lacks tuple predicates; do a straightforward reconciliation.
        all_repo_rows = conn.execute("SELECT id FROM repos").fetchall()
        for repo_row in all_repo_rows:
            if int(repo_row["id"]) in seen_repo_ids:
                continue
            conn.execute("DELETE FROM repos WHERE id = ?", (repo_row["id"],))
        reference_resolution = _resolve_references(conn)
        stats["reference_resolution"] = reference_resolution
        audit(conn, "index.completed", stats)
        audit(conn, "references.resolved", reference_resolution)
        stats["search_index_documents"] = rebuild_search_index(conn)
        conn.commit()
    finally:
        conn.close()
    return stats

