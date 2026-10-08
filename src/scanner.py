"""Lectura del historial de Git y detección de secretos en las líneas añadidas.

El historial se procesa en streaming, línea a línea, sin cargarlo entero en memoria.
El valor de un secreto no sale de este módulo: de cada coincidencia solo se conserva
una versión enmascarada.
"""

import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from config import Config

# Marcador para ignorar una línea. Solo afecta a las líneas que ya lo llevaban cuando
# se añadieron: no oculta nada del historial anterior.
IGNORE_MARKER = "secret-sweep: ignore"

# Cabecera de bloque de un diff: @@ -12,3 +40,5 @@  -> nos interesa el 40
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class Finding:
    """Un posible secreto encontrado en el historial. Es inmutable.

    Attributes:
        rule: Nombre de la regla que lo detectó.
        commit: Hash completo del commit donde se añadió la línea.
        file: Ruta del archivo, relativa al repositorio y con `/` también en Windows.
        line: Número de línea (desde 1) en la versión del archivo de ese commit, no
            en la versión actual.
        preview: Primeros caracteres del texto detectado, enmascarados (ver `mask`).
            Nunca contiene el valor completo.
    """

    rule: str
    commit: str
    file: str
    line: int
    preview: str  # versión enmascarada, nunca el secreto completo


def mask(secret: str) -> str:
    """Enmascara un texto: sus 4 primeros caracteres y siempre 8 asteriscos.

    El número de asteriscos es fijo, así que el resultado no revela la longitud del
    original. Está pensada para coincidencias largas: con una de 4 caracteres o
    menos mostraría el texto entero.
    """
    return secret[:4] + "*" * 8


def check_line(
    text: str, commit: str, file: str, line_no: int, config: Config
) -> Iterator[Finding]:
    """Evalúa una línea añadida contra todas las reglas.

    Si la línea lleva el marcador `secret-sweep: ignore`, no se evalúa ninguna regla.
    Se informa como máximo de una coincidencia por regla y línea: si una misma regla
    coincide dos veces en la línea, solo se devuelve la primera.

    Args:
        text: Contenido de la línea, sin el `+` inicial del diff.
        commit: Hash del commit en el que se añadió.
        file: Ruta del archivo.
        line_no: Número de línea en la versión del archivo de ese commit.
        config: Reglas y exclusiones.

    Yields:
        Un `Finding` por cada regla que coincide y no está excluida.
    """
    if IGNORE_MARKER in text:
        return
    for name, pattern in config.rules.items():
        match = pattern.search(text)
        if match and not config.value_allowed(match.group(0)):
            yield Finding(name, commit, file, line_no, mask(match.group(0)))


def scan_repo(repo: Path, config: Config) -> list[Finding]:
    """Busca secretos en todo el historial de un repositorio.

    Recorre todas las ramas con `git log --all -p` y analiza solo las líneas
    añadidas, porque un secreto "nace" cuando se añade. La salida de Git se lee en
    streaming, línea a línea, así que no se carga el historial entero en memoria.
    Los archivos cuya ruta está excluida en `config` se saltan sin analizarlos.

    Args:
        repo: Carpeta del repositorio (debe contener `.git`).
        config: Reglas y exclusiones ya compiladas.

    Returns:
        Todos los hallazgos, sin aplicar ninguna línea base.

    Raises:
        ValueError: Si `repo` no contiene una carpeta `.git`.
        RuntimeError: Si Git termina con error al leer el historial.
        FileNotFoundError: Si Git no está instalado o no está en el `PATH`.
    """
    if not (repo / ".git").exists():
        raise ValueError(f"{repo} no parece un repositorio Git")

    cmd = [
        "git", "-C", str(repo), "log",
        "--all", "-p", "--no-color", "--unified=0",
        "--format=commit:%H",
    ]
    findings: list[Finding] = []
    commit = ""
    file = None
    line_no = 0

    with subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as proc:
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")

            if line.startswith("commit:"):
                commit = line[len("commit:"):]
            elif line.startswith("+++ "):
                # "+++ b/ruta" = archivo con cambios (nuevo o modificado);
                # "+++ /dev/null" = archivo borrado
                file = line[6:] if line.startswith("+++ b/") else None
                if file and config.path_allowed(file):
                    file = None  # archivo excluido: se ignoran todas sus líneas
            elif line.startswith("@@"):
                match = HUNK_RE.match(line)
                if match:
                    line_no = int(match.group(1))
            elif line.startswith("+") and file:
                findings.extend(check_line(line[1:], commit, file, line_no, config))
                line_no += 1

    if proc.returncode != 0:
        raise RuntimeError("git devolvió un error al leer el historial")
    return findings