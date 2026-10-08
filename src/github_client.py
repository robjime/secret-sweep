"""Acceso a GitHub: listar repositorios, clonarlos o actualizarlos y enlazar hallazgos.

El token de GitHub se trata como un secreto:

- No se pone en la URL de clonado, así que no queda guardado en `.git/config`: se
  pasa a Git como una cabecera HTTP temporal.
- Si un comando de Git falla, se sustituye el token por `***` en el mensaje de error.

Límites: mientras Git se ejecuta, la cabecera es visible en los argumentos del
proceso para otros programas del mismo equipo, y el saneado de errores solo
reconoce el token literal (no su forma codificada en Base64).
"""

import base64
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests

from scanner import Finding

API_URL = "https://api.github.com"
PER_PAGE = 100


@dataclass(frozen=True)
class RepoInfo:
    """Datos básicos de un repositorio, tal como los devuelve la API de GitHub.

    Estos valores deben venir de la API: `name` se usa como nombre de carpeta y
    `clone_url` es el servidor al que se enviará el token.

    Attributes:
        name: Nombre del repositorio.
        clone_url: URL HTTPS para clonarlo, sin credenciales.
        private: True si el repositorio no es público.
        html_url: Enlace web del repositorio (vacío si no se conoce).
    """

    name: str
    clone_url: str
    private: bool
    html_url: str = ""


def get_token() -> str:
    """Lee el token de GitHub de la variable de entorno `GITHUB_TOKEN`.

    No lee el archivo `.env`: `main.py` lo carga antes de llamar aquí. Se quitan los
    espacios y saltos de línea del principio y del final.

    Returns:
        El token.

    Raises:
        ValueError: Si `GITHUB_TOKEN` no existe o está vacía.
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        raise ValueError(
            "Falta GITHUB_TOKEN. Crea un token de solo lectura en GitHub y "
            "guárdalo en el archivo .env (ver .env.example)."
        )
    return token


def _check_response(response) -> None:
    """Convierte las respuestas HTTP de error de la API en mensajes claros.

    Args:
        response: Respuesta de `requests` (o un objeto equivalente).

    Raises:
        RuntimeError: Si el token es rechazado (401), si se agotó el límite de
            peticiones (403 con `X-RateLimit-Remaining` a 0) o si el código es
            cualquier otro distinto de 200.
    """
    if response.status_code == 401:
        raise RuntimeError(
            "GitHub rechazó el token (401): ¿ha caducado o está mal copiado?"
        )
    if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
        raise RuntimeError("Límite de peticiones de GitHub alcanzado; inténtalo más tarde.")
    if response.status_code != 200:
        raise RuntimeError(f"GitHub devolvió un error inesperado ({response.status_code}).")


def list_repos(token: str, include_forks: bool = False, session=None) -> list[RepoInfo]:
    """Lista los repositorios de los que el usuario autenticado es propietario.

    Solo los propios (`affiliation=owner`): no incluye los de organizaciones ni los
    compartidos como colaborador. Los forks se excluyen salvo que se pida lo
    contrario. Pide las páginas de 100 en 100 hasta agotarlas, con un timeout de
    15 segundos por petición.

    Args:
        token: Token de acceso a la API.
        include_forks: Si es True, incluye también los forks.
        session: Objeto con un método `get` como el de `requests.Session`; sirve para
            sustituirlo por uno falso en las pruebas.

    Returns:
        Un `RepoInfo` por repositorio.

    Raises:
        RuntimeError: Si falla la conexión o la API responde con un error (ver
            `_check_response`).
    """
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
            repos.append(
                RepoInfo(item["name"], item["clone_url"], item["private"], item.get("html_url", ""))
            )
        if len(items) < PER_PAGE:
            return repos
        page += 1


def _auth_args(token: str | None) -> list[str]:
    """Devuelve los argumentos de Git que pasan el token como cabecera HTTP.

    La cabecera (`http.extraheader`, en formato Basic con el token en Base64, que es
    solo una codificación y no lo protege) vive únicamente en la línea de comandos de
    ese Git: no se escribe en `.git/config`. Pero Git la envía a cualquier servidor
    HTTP que contacte ese comando, y es visible en la lista de procesos mientras se
    ejecuta.

    Args:
        token: Token de acceso, o None si no hay autenticación.

    Returns:
        Los argumentos a añadir tras `git`, o una lista vacía si no hay token.
    """
    if not token:
        return []
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return ["-c", f"http.extraheader=Authorization: Basic {basic}"]


def _run_git(args: list[str], token: str | None) -> None:
    """Ejecuta un comando de Git y convierte un fallo en `RuntimeError`.

    Desactiva las peticiones interactivas de contraseña (`GIT_TERMINAL_PROMPT=0`)
    para que Git falle en vez de quedarse esperando. Si el comando falla, el mensaje
    de error de Git (`stderr`) se limpia: cada aparición literal del token se
    sustituye por `***`. No se reconoce el token codificado en Base64.

    Args:
        args: Argumentos de `git`, sin incluir la autenticación.
        token: Token a ocultar en el mensaje de error, o None.

    Raises:
        RuntimeError: Si Git termina con un código distinto de cero.
        FileNotFoundError: Si Git no está instalado o no está en el `PATH`.
    """
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
    """Clona un repositorio en `workdir`, o actualiza su historial si ya está.

    Si `workdir/<nombre>/.git` existe, hace `fetch --all --prune`, que trae el
    historial nuevo sin tocar los archivos de la carpeta; si no, hace `clone`. Al
    escáner le basta, porque solo lee el historial.

    Args:
        repo: Repositorio a descargar.
        workdir: Carpeta donde se guardan los repositorios (se crea si no existe).
        token: Token de GitHub para repositorios privados, o None.

    Returns:
        La carpeta del repositorio (`workdir / repo.name`). Es relativa si `workdir`
        lo es.

    Raises:
        RuntimeError: Si Git falla.
        FileNotFoundError: Si Git no está instalado.
        OSError: Si no se puede crear `workdir` (por ejemplo, por falta de permisos).
    """
    dest = workdir / repo.name
    workdir.mkdir(parents=True, exist_ok=True)
    if (dest / ".git").exists():
        args = ["-C", str(dest), "fetch", "--all", "--prune", "--quiet"]
    else:
        args = ["clone", "--quiet", repo.clone_url, str(dest)]
    _run_git(args, token)
    return dest


def finding_url(html_url: str, finding: Finding) -> str:
    """Construye un enlace permanente a la línea de un hallazgo en GitHub.

    Usa `/blob/<commit>/<archivo>#L<línea>`, así que muestra el archivo tal como era
    cuando se añadió el secreto, aunque después se haya movido o borrado. Solo
    funciona mientras ese commit exista en GitHub (no tras reescribir el historial)
    y, en repositorios privados, para quien tenga acceso.

    Args:
        html_url: Enlace web del repositorio.
        finding: Hallazgo del que se quiere el enlace.

    Returns:
        El enlace, o una cadena vacía si `html_url` está vacío.
    """
    if not html_url:
        return ""
    return f"{html_url}/blob/{finding.commit}/{quote(finding.file)}#L{finding.line}"