"""Project configuration handling."""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass


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

    @property
    def atlas_dir(self) -> pathlib.Path:
        return self.project_root / ATLAS_DIR

    @property
    def db_path(self) -> pathlib.Path:
        return self.atlas_dir / DB_NAME


def default_config(project_root: pathlib.Path) -> dict:
    return {
        "project": project_root.name,
        "subsystems": [
            {"name": "default", "repos": ["."]}
        ],
        "languages": ["python", "typescript", "javascript", "go", "java"],
        "exclude": [
            "node_modules",
            "dist",
            "target",
            ".venv",
            "venv",
            "__pycache__",
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
    return ProjectConfig(
        project_root=root,
        project=project.strip(),
        subsystems=tuple(normalized_subsystems),
        languages=normalized_languages,
        exclude=tuple(exclude),
    )

