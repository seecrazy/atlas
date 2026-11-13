"""Optional tree-sitter parsing backend with graceful builtin fallback."""

from __future__ import annotations

import pathlib
import re
from dataclasses import replace

try:
    from tree_sitter_language_pack import get_parser
    TREE_SITTER_AVAILABLE = True
except Exception:
    get_parser = None
    TREE_SITTER_AVAILABLE = False

LANGUAGE_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".java": "java",
    ".rs": "rust",
}

SYMBOL_FIELDS = {
    "python": {
        "function_definition": ("function", "name"),
        "class_definition": ("class", "name"),
    },
    "typescript": {
        "function_declaration": ("function", "name"),
        "class_declaration": ("class", "name"),
        "method_definition": ("method", "name"),
        "interface_declaration": ("type", "name"),
        "type_alias_declaration": ("type", "name"),
        "enum_declaration": ("type", "name"),
    },
    "javascript": {
        "function_declaration": ("function", "name"),
        "class_declaration": ("class", "name"),
        "method_definition": ("method", "name"),
    },
    "go": {
        "function_declaration": ("function", "name"),
        "method_declaration": ("method", "name"),
        "type_spec": ("type", "name"),
    },
    "java": {
        "class_declaration": ("class", "name"),
        "interface_declaration": ("interface", "name"),
        "enum_declaration": ("enum", "name"),
        "method_declaration": ("method", "name"),
    },
    "rust": {
        "function_item": ("function", "name"),
        "struct_item": ("type", "name"),
        "enum_item": ("type", "name"),
        "trait_item": ("trait", "name"),
        "impl_item": ("impl", "type"),
    },
}


def tree_sitter_available() -> bool:
    return TREE_SITTER_AVAILABLE


def _is_exported(node, language: str) -> bool:
    parent = node.parent
    if parent is not None and parent.type in {"export_statement", "export"}:
        return True
    if language == "python":
        return parent is not None and parent.type == "module"
    if language == "go":
        name_node = node.child_by_field_name("name") or node.child_by_field_name("type")
        return bool(name_node and name_node.text and name_node.text.decode("utf-8")[:1].isupper())
    if language == "java":
        text = node.text.decode("utf-8", errors="ignore")
        return "public" in text.split("\n", 1)[0]
    if language == "rust":
        text = node.text.decode("utf-8", errors="ignore")
        return "pub" in text.split("\n", 1)[0]
    return False


def _parent_qualified(node, language: str, package: str | None = None) -> str | None:
    if language == "go" and node.type == "method_declaration":
        receiver = node.child_by_field_name("receiver")
        if receiver is not None and receiver.text:
            import re

            identifiers = re.findall(r"[A-Za-z_][\w]*", receiver.text.decode("utf-8"))
            if identifiers:
                receiver = identifiers[-1]
                return f"{package}.{receiver}" if package else receiver
    current = node.parent
    while current is not None:
        rules = SYMBOL_FIELDS.get(language, {})
        if current.type in rules:
            name_node = current.child_by_field_name("name") or current.child_by_field_name("type")
            if name_node is not None and name_node.text:
                return name_node.text.decode("utf-8")
        current = current.parent
    return None


def extract_tree_sitter_symbols(path: pathlib.Path, language: str, module_name: str) -> tuple[Symbol, ...] | None:
    from .indexer import Symbol

    if not TREE_SITTER_AVAILABLE:
        return None
    parser = get_parser(language)
    tree = parser.parse(path.read_bytes())
    symbols: list[Symbol] = []
    rules = SYMBOL_FIELDS.get(language, {})
    if not rules:
        return ()
    package = None
    if language == "go":
        package_match = re.search(
            r"^\s*package\s+([A-Za-z_][\w]*)",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        package = package_match.group(1) if package_match else path.stem

    def visit(node) -> None:
        if node.type in rules:
            kind, field_name = rules[node.type]
            name_node = node.child_by_field_name(field_name)
            if name_node is not None and name_node.text:
                name = name_node.text.decode("utf-8")
                parent_name = _parent_qualified(node, language, package)
                qualified = f"{parent_name}.{name}" if parent_name else f"{module_name}.{name}"
                symbols.append(
                    Symbol(
                        name=name,
                        qualified_name=qualified,
                        kind=kind,
                        start_line=node.start_point[0] + 1,
                        end_line=node.end_point[0] + 1,
                        exported=_is_exported(node, language),
                        parent_qualified=parent_name,
                    )
                )
        for child in node.children:
            visit(child)

    visit(tree.root_node)
    if language == "java":
        package_match = re.search(r"^\s*package\s+([\w.]+)\s*;", path.read_text(encoding="utf-8"), re.MULTILINE)
        package = package_match.group(1) if package_match else None
        if package:
            symbols = [
                replace(
                    symbol,
                    parent_qualified=(
                        f"{package}.{symbol.parent_qualified}"
                        if symbol.parent_qualified and not symbol.parent_qualified.startswith(package + ".")
                        else symbol.parent_qualified
                    ),
                    qualified_name=(
                        symbol.qualified_name
                        if symbol.qualified_name.startswith(package + ".")
                        else f"{package}.{symbol.qualified_name}"
                    ),
                )
                for symbol in symbols
            ]
    return tuple(symbols)


def extract_with_tree_sitter(path: pathlib.Path, module_name: str, language: str) -> FileRecord | None:
    from .indexer import (
        FileRecord,
        Symbol,
        extract_go,
        extract_java,
        extract_python,
        extract_rust,
        extract_ts_js,
    )

    symbols = extract_tree_sitter_symbols(path, language, module_name)
    if symbols is None:
        return None
    if language == "python":
        record, _ = extract_python(path, module_name)
        return replace(record, symbols=symbols)
    if language == "go":
        record, _ = extract_go(path, module_name)
        return replace(record, symbols=symbols)
    if language == "java":
        record, _ = extract_java(path, module_name)
        return replace(record, symbols=symbols)
    if language == "rust":
        record, _ = extract_rust(path, module_name)
        return replace(record, symbols=symbols)
    record, _ = extract_ts_js(path, module_name)
    return replace(record, symbols=symbols)
