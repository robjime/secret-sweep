import base64
import subprocess

import pytest

import github_client
from config import load_config
from github_client import RepoInfo, clone_or_update, get_token, list_repos
from baseline import Baseline
from main import main, run_github

# Token falso construido en tiempo de ejecución: así no aparece literal en el código.
FAKE_TOKEN = "ghp_" + "x" * 36

RULES_YAML = r"""
rules:
  - name: AWS Access Key ID
    pattern: '\bAKIA[0-9A-Z]{16}\b'
"""


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
        self.headers = headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(params)
        return self.responses.pop(0)


def repo_json(name, fork=False, private=False):
    return {
        "name": name,
        "clone_url": f"https://github.com/yo/{name}.git",
        "fork": fork,
        "private": private,
    }


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        check=True, capture_output=True,
    )


def make_remote(tmp_path, nombre="remote", contenido="hola\n"):
    remote = tmp_path / nombre
    remote.mkdir()
    git(remote, "init")
    (remote / "a.txt").write_text(contenido, encoding="utf-8")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "inicial")
    return remote


# ---------- API de GitHub (sin red: sesión simulada) ----------

def test_list_repos_pagina_y_excluye_forks(monkeypatch):
    monkeypatch.setattr(github_client, "PER_PAGE", 2)
    session = FakeSession([
        FakeResponse(payload=[repo_json("a"), repo_json("b", fork=True)]),
        FakeResponse(payload=[repo_json("c", private=True)]),
    ])

    repos = list_repos(FAKE_TOKEN, session=session)

    assert [r.name for r in repos] == ["a", "c"]
    assert [p["page"] for p in session.calls] == [1, 2]


def test_list_repos_incluye_forks_si_se_pide(monkeypatch):
    monkeypatch.setattr(github_client, "PER_PAGE", 5)
    session = FakeSession([FakeResponse(payload=[repo_json("a"), repo_json("b", fork=True)])])

    repos = list_repos(FAKE_TOKEN, include_forks=True, session=session)

    assert [r.name for r in repos] == ["a", "b"]


def test_token_rechazado():
    session = FakeSession([FakeResponse(status_code=401)])
    with pytest.raises(RuntimeError, match="token"):
        list_repos(FAKE_TOKEN, session=session)


def test_limite_de_peticiones():
    session = FakeSession([FakeResponse(status_code=403, headers={"X-RateLimit-Remaining": "0"})])
    with pytest.raises(RuntimeError, match="Límite"):
        list_repos(FAKE_TOKEN, session=session)


def test_get_token_sin_variable(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        get_token()


# ---------- Clonado (con repos locales, sin red) ----------

def test_clona_y_luego_actualiza(tmp_path):
    remote = make_remote(tmp_path)
    repo = RepoInfo("remote", str(remote), False)
    workdir = tmp_path / "work"

    dest = clone_or_update(repo, workdir)
    assert (dest / ".git").exists()

    (remote / "b.txt").write_text("nuevo\n", encoding="utf-8")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "segundo")
    clone_or_update(repo, workdir)  # esta vez hace fetch

    log = subprocess.run(
        ["git", "-C", str(dest), "log", "--all", "--oneline"],
        capture_output=True, text=True,
    ).stdout
    assert len(log.splitlines()) == 2


def test_error_de_clonado_no_filtra_el_token(tmp_path):
    repo = RepoInfo("no-existe", str(tmp_path / "nada"), False)
    codificado = base64.b64encode(f"x-access-token:{FAKE_TOKEN}".encode()).decode()

    with pytest.raises(RuntimeError) as error:
        clone_or_update(repo, tmp_path / "work", token=FAKE_TOKEN)

    assert FAKE_TOKEN not in str(error.value)
    assert codificado not in str(error.value)


# ---------- Orquestación y línea de comandos ----------

def test_run_github_escanea_y_resume(tmp_path, monkeypatch, capsys):
    remote = make_remote(tmp_path, contenido='AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES_YAML, encoding="utf-8")
    config = load_config(rules)

    monkeypatch.setenv("GITHUB_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(
        "main.list_repos",
        lambda token, include_forks=False: [RepoInfo("remote", str(remote), False)],
    )

    codigo = run_github(
        config, tmp_path / "work", False, Baseline(tmp_path / "baseline.json"), None
    )

    salida = capsys.readouterr().out
    assert codigo == 1
    assert "remote: 1 hallazgo(s) nuevo(s)" in salida
    assert "Resumen: 1 repo(s), 1 nuevo(s), 0 ya revisado(s), 0 error(es)." in salida


def test_cli_exige_repo_o_github(monkeypatch):
    monkeypatch.setattr("sys.argv", ["main"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_cli_accept_no_se_combina_con_no_baseline(monkeypatch):
    monkeypatch.setattr("sys.argv", ["main", ".", "--accept", "motivo", "--no-baseline"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2