import subprocess
from pathlib import Path

import pytest

from config import load_config
from scanner import scan_repo

RULES_YAML = r"""
rules:
  - name: AWS Access Key ID
    pattern: '\bAKIA[0-9A-Z]{16}\b'
"""

DEFAULT_RULES = Path(__file__).resolve().parent.parent / "src" / "rules.yaml"

KEY_LINE = 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        check=True, capture_output=True,
    )


def commit_file(repo, name, content):
    archivo = repo / name
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(content, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", f"cambios en {name}")


def make_config(tmp_path, allowlist=""):
    path = tmp_path / "rules.yaml"
    path.write_text(RULES_YAML + allowlist, encoding="utf-8")
    return load_config(path)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init")
    return path


def test_detecta_secreto_borrado(repo, tmp_path):
    commit_file(repo, "config.py", KEY_LINE)
    commit_file(repo, "config.py", 'AWS_KEY = "cambiar"\n')

    findings = scan_repo(repo, make_config(tmp_path))

    assert any(f.rule == "AWS Access Key ID" for f in findings)
    assert all("AKIAIOSFODNN7EXAMPLE" not in f.preview for f in findings)


def test_allowlist_por_ruta(repo, tmp_path):
    commit_file(repo, "tests/datos.py", KEY_LINE)
    config = make_config(tmp_path, "allowlist:\n  paths:\n    - 'tests/*'\n")

    assert scan_repo(repo, config) == []


def test_allowlist_por_patron(repo, tmp_path):
    commit_file(repo, "config.py", KEY_LINE)
    config = make_config(
        tmp_path, "allowlist:\n  patterns:\n    - 'AKIAIOSFODNN7EXAMPLE'\n"
    )

    assert scan_repo(repo, config) == []


def test_marcador_en_la_linea(repo, tmp_path):
    commit_file(repo, "config.py", KEY_LINE.rstrip("\n") + "  # secret-sweep: ignore\n")

    assert scan_repo(repo, make_config(tmp_path)) == []


def test_regex_invalida_da_error_claro(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text("rules:\n  - name: Rota\n    pattern: '('\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Rota"):
        load_config(path)


def test_regla_de_token_github_fine_grained():
    config = load_config(DEFAULT_RULES)
    # Token falso construido en tiempo de ejecución para que no aparezca literal.
    falso = "github_pat_" + "A" * 30

    assert config.rules["GitHub token (fine-grained)"].search("clave: " + falso)