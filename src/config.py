import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    rules: dict[str, re.Pattern]
    allow_paths: tuple[str, ...] = ()
    allow_patterns: tuple[re.Pattern, ...] = ()

    def path_allowed(self, file: str) -> bool:
        return any(fnmatch(file, pattern) for pattern in self.allow_paths)

    def value_allowed(self, text: str) -> bool:
        return any(p.search(text) for p in self.allow_patterns)


def _compile(pattern: str, where: str) -> re.Pattern:
    try:
        return re.compile(pattern)
    except re.error as error:
        raise ValueError(f"{where}: expresión regular inválida ({error})") from error


def load_config(path: Path) -> Config:
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as error:
        raise ValueError(f"No se pudo leer {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ValueError(f"YAML inválido en {path}: {error}") from error

    if not isinstance(data, dict) or not data.get("rules"):
        raise ValueError(f"{path}: falta la sección 'rules'")

    rules: dict[str, re.Pattern] = {}
    for entry in data["rules"]:
        try:
            name, pattern = entry["name"], entry["pattern"]
        except (KeyError, TypeError):
            raise ValueError(f"{path}: cada regla necesita 'name' y 'pattern'") from None
        rules[name] = _compile(pattern, f"Regla '{name}'")

    allow = data.get("allowlist") or {}
    return Config(
        rules=rules,
        allow_paths=tuple(allow.get("paths") or ()),
        allow_patterns=tuple(
            _compile(p, "allowlist") for p in allow.get("patterns") or ()
        ),
    )