"""Project configuration handling."""

from __future__ import annotations

import json
import pathlib
import os
from dataclasses import dataclass, field


ATLAS_DIR = ".atlas"
CONFIG_NAME = "config.json"
DB_NAME = "index.db"


class ConfigError(ValueError):
    """Raised when an ATLAS project configuration is missing or invalid."""


@dataclass(frozen=True)
class ProjectConfig:
    project_root: pathlib.Path
    project: str
    subsystems: tuple[dict, ...]
    languages: tuple[str, ...]
    exclude: tuple[str, ...]
    contracts: tuple[dict, ...] = ()
    doc_paths: tuple[str, ...] = ("docs",)
    max_workers: int = 4
    budgets: dict = field(default_factory=lambda: {
        "max_seconds": 300.0,
        "max_calls": 50,
        "max_tokens": 50000,
    })
    llm: dict = field(default_factory=lambda: {
        "provider": "none",
        "base_url_env": "ATLAS_LLM_BASE_URL",
        "api_key_env": "ATLAS_LLM_API_KEY",
        "model_env": "ATLAS_LLM_MODEL",
    })
    parser_backend: str = "auto"
    allowed_test_commands: tuple[str, ...] = (
        "pytest",
        "python -m pytest",
        "python3 -m pytest",
        "npm test",
        "go test ./...",
        "mvn test",
    )

    @property
    def atlas_dir(self) -> pathlib.Path:
        return self.project_root / ATLAS_DIR

    @property
    def db_path(self) -> pathlib.Path:
        return self.atlas_dir / DB_NAME


def default_config(project_root: pathlib.Path) -> dict:
    budgets = {
        "max_seconds": 300.0,
        "max_calls": 50,
        "max_tokens": 50000,
    }
    return {
        "project": project_root.name,
        "subsystems": [
            {"name": "default", "repos": ["."]}
        ],
        "languages": ["python", "typescript", "javascript", "go", "java", "rust"],
        "exclude": [
            "node_modules",
            "dist",
            "target",
            ".venv",
            "venv",
            "__pycache__",
        ],
        "doc_paths": ["docs", "adr"],
        "max_workers": 4,
        "budgets": budgets,
        "llm": {
            "provider": "none",
            "base_url_env": "ATLAS_LLM_BASE_URL",
            "api_key_env": "ATLAS_LLM_API_KEY",
            "model_env": "ATLAS_LLM_MODEL",
        },
        "parser_backend": "auto",
        "allowed_test_commands": [
            "pytest",
            "python -m pytest",
            "python3 -m pytest",
            "npm test",
            "go test ./...",
            "mvn test",
        ],
    }


def write_default_config(project_root: pathlib.Path, *, overwrite: bool = False) -> pathlib.Path:
    root = pathlib.Path(project_root).resolve()
    atlas_dir = root / ATLAS_DIR
    atlas_dir.mkdir(parents=True, exist_ok=True)
    path = atlas_dir / CONFIG_NAME
    if path.exists() and not overwrite:
        return path
    path.write_text(
        json.dumps(default_config(root), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_config(project_root: str | pathlib.Path) -> ProjectConfig:
    root = pathlib.Path(project_root).expanduser().resolve()
    path = root / ATLAS_DIR / CONFIG_NAME
    if not path.is_file():
        raise ConfigError(f"ATLAS config not found: {path}. Run `atlas init` first.")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read ATLAS config {path}: {exc}") from exc

    project = raw.get("project")
    subsystems = raw.get("subsystems")
    languages = raw.get("languages")
    exclude = raw.get("exclude", [])
    if not isinstance(project, str) or not project.strip():
        raise ConfigError("config.project must be a non-empty string")
    if not isinstance(subsystems, list) or not subsystems:
        raise ConfigError("config.subsystems must be a non-empty list")
    if not isinstance(languages, list) or not languages:
        raise ConfigError("config.languages must be a non-empty list")
    if not isinstance(exclude, list) or not all(isinstance(item, str) for item in exclude):
        raise ConfigError("config.exclude must be a list of strings")

    seen_repos: set[str] = set()
    normalized_subsystems: list[dict] = []
    for index, subsystem in enumerate(subsystems):
        if not isinstance(subsystem, dict):
            raise ConfigError(f"config.subsystems[{index}] must be an object")
        name = subsystem.get("name")
        repos = subsystem.get("repos")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f"config.subsystems[{index}].name must be a non-empty string")
        if not isinstance(repos, list) or not repos or not all(isinstance(r, str) and r.strip() for r in repos):
            raise ConfigError(f"config.subsystems[{index}].repos must be a non-empty list")
        for repo in repos:
            if repo in seen_repos:
                raise ConfigError(f"repo is registered more than once: {repo}")
            seen_repos.add(repo)
            repo_dir = (root / repo).resolve()
            if not repo_dir.is_dir():
                raise ConfigError(f"repo directory does not exist: {repo_dir}")
        normalized_subsystems.append({"name": name.strip(), "repos": list(repos)})

    normalized_languages = tuple(str(item).lower() for item in languages)
    contracts = raw.get("contracts", [])
    if not isinstance(contracts, list) or not all(isinstance(item, dict) for item in contracts):
        raise ConfigError("config.contracts must be a list of objects")
    doc_paths = raw.get("doc_paths", ["docs"])
    if not isinstance(doc_paths, list) or not all(isinstance(item, str) for item in doc_paths):
        raise ConfigError("config.doc_paths must be a list of strings")
    max_workers = raw.get("max_workers", 4)
    if not isinstance(max_workers, int) or not 1 <= max_workers <= 64:
        raise ConfigError("config.max_workers must be an integer between 1 and 64")
    default_budgets = default_config(root)["budgets"]
    budgets = raw.get("budgets", default_budgets)
    if not isinstance(budgets, dict):
        raise ConfigError("config.budgets must be an object")
    for key in ("max_seconds", "max_calls", "max_tokens"):
        value = budgets.get(key, default_budgets[key])
        if not isinstance(value, (int, float)) or value <= 0:
            raise ConfigError(f"config.budgets.{key} must be a positive number")
    default_llm = default_config(root)["llm"]
    llm = raw.get("llm", default_llm)
    if not isinstance(llm, dict):
        raise ConfigError("config.llm must be an object")
    parser_backend = str(raw.get("parser_backend", "auto")).lower()
    if parser_backend not in {"auto", "tree-sitter", "builtin"}:
        raise ConfigError("config.parser_backend must be auto, tree-sitter, or builtin")
    allowed_test_commands = raw.get(
        "allowed_test_commands",
        default_config(root)["allowed_test_commands"],
    )
    if not isinstance(allowed_test_commands, list) or not all(
        isinstance(item, str) and item.strip() for item in allowed_test_commands
    ):
        raise ConfigError(
            "config.allowed_test_commands must be a list of non-empty command strings"
        )
    return ProjectConfig(
        project_root=root,
        project=project.strip(),
        subsystems=tuple(normalized_subsystems),
        languages=normalized_languages,
        exclude=tuple(exclude),
        contracts=tuple(contracts),
        doc_paths=tuple(doc_paths),
        max_workers=max_workers,
        budgets=budgets,
        llm=llm,
        parser_backend=parser_backend,
        allowed_test_commands=tuple(item.strip() for item in allowed_test_commands),
    )

