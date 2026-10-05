import base64
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import requests

API_URL = "https://api.github.com"
PER_PAGE = 100


@dataclass(frozen=True)
class RepoInfo:
    name: str
    clone_url: str
    private: bool


def get_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "Falta GITHUB_TOKEN. Crea un token de solo lectura en GitHub y "
            "guárdalo en el archivo .env (ver .env.example)."
        )
    return token


def _check_response(response) -> None:
    if response.status_code == 401:
        raise RuntimeError(
            "GitHub rechazó el token (401): ¿ha caducado o está mal copiado?"
        )
    if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
        raise RuntimeError("Límite de peticiones de GitHub alcanzado; inténtalo más tarde.")
    if response.status_code != 200:
        raise RuntimeError(f"GitHub devolvió un error inesperado ({response.status_code}).")


def list_repos(token: str, include_forks: bool = False, session=None) -> list[RepoInfo]:
    session = session or requests.Session()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    repos: list[RepoInfo] = []
    page = 1
    while True:
        try:
            response = session.get(
                f"{API_URL}/user/repos",
                headers=headers,
                params={"per_page": PER_PAGE, "page": page, "affiliation": "owner"},
                timeout=15,
            )
        except requests.RequestException as error:
            raise RuntimeError(f"No se pudo conectar con GitHub: {error}") from None
        _check_response(response)

        items = response.json()
        for item in items:
            if item["fork"] and not include_forks:
                continue
            repos.append(RepoInfo(item["name"], item["clone_url"], item["private"]))
        if len(items) < PER_PAGE:
            return repos
        page += 1


def _auth_args(token: str | None) -> list[str]:
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraheader=Authorization: Basic {basic}"]


def _run_git(args: list[str], token: str | None) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    result = subprocess.run(
        ["git", *_auth_args(token), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or "git falló sin mensaje"
        if token:
            message = message.replace(token, "***")
        raise RuntimeError(message)


def clone_or_update(repo: RepoInfo, workdir: Path, token: str | None = None) -> Path:
    dest = workdir / repo.name
    workdir.mkdir(parents=True, exist_ok=True)
    if (dest / ".git").exists():
        args = ["-C", str(dest), "fetch", "--all", "--prune", "--quiet"]
    else:
        args = ["clone", "--quiet", repo.clone_url, str(dest)]
    _run_git(args, token)
    return dest