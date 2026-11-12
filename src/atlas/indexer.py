"""File scanning, symbol extraction, and SQLite indexing."""

from __future__ import annotations

import ast
import hashlib
import pathlib
import re
import sqlite3
from dataclasses import dataclass
from dataclasses import replace

from .config import ProjectConfig
from .db import audit, connect, utcnow


LANGUAGE_EXTENSIONS = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
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


def extract_python(path: pathlib.Path, module_name: str) -> tuple[FileRecord, bytes]:
    content = path.read_bytes()
    try:
        text = content.decode("utf-8")
        tree = ast.parse(text, filename=str(path))
    except (UnicodeDecodeError, SyntaxError):
        return FileRecord(
            path=path.as_posix(),
            language="python",
            module_name=module_name,
            content_hash=content_digest(content),
            mtime=path.stat().st_mtime,
            symbols=(),
            imports=(),
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


def scan_file(path: pathlib.Path, display_path: pathlib.Path | None = None) -> FileRecord | None:
    language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())
    if language not in {"python", "typescript", "javascript"}:
        return None
    display_path = display_path or path
    module_name = module_name_from_path(display_path)
    if language == "python":
        record, _ = extract_python(path, module_name)
        return replace(record, path=display_path.as_posix())
    record, _ = extract_ts_js(path, module_name)
    record = replace(record, path=display_path.as_posix())
    return record


def scan_repo(repo_dir: pathlib.Path, project_config: ProjectConfig) -> list[FileRecord]:
    records: list[FileRecord] = []
    for path in sorted(repo_dir.rglob("*")):
        if not path.is_file():
            continue
        if is_excluded(path, project_config):
            continue
        record = scan_file(path, path.relative_to(repo_dir))
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


def index_project(config: ProjectConfig) -> dict:
    conn = connect(config.db_path, create=True)
    stats = {
        "repos": 0,
        "files_scanned": 0,
        "files_changed": 0,
        "files_unchanged": 0,
        "symbols": 0,
        "imports": 0,
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
                for record in scan_repo(repo_dir, config):
                    rel_path = record.path
                    # Project-wide stable paths include the repo name. This makes
                    # scope boundaries and query output unambiguous across repos.
                    record = replace(record, path=f"{repo_name}/{rel_path}")
                    indexed_paths.add(rel_path)
                    indexed_paths.add(record.path)
                    stable_path = record.path
                    stats["files_scanned"] += 1
                    stats["symbols"] += len(record.symbols)
                    stats["imports"] += len(record.imports)
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
                    file_id = _insert_file_record(conn, repo_id, record)
                    stats["files_changed"] += 1

                # Remove files deleted from the working tree. This deliberately
                # keeps unchanged rows untouched so incremental indexing stays cheap.
                existing_paths = {
                    row["path"]
                    for row in conn.execute(
                        "SELECT path FROM files WHERE repo_id = ?", (repo_id,)
                    )
                }
                for deleted_path in existing_paths - indexed_paths:
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
        audit(conn, "index.completed", stats)
        conn.commit()
    finally:
        conn.close()
    return stats

