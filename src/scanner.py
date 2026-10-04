import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from config import Config

IGNORE_MARKER = "secret-sweep: ignore"

# Cabecera de bloque de un diff: @@ -12,3 +40,5 @@  -> nos interesa el 40
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class Finding:
    rule: str
    commit: str
    file: str
    line: int
    preview: str  # versión enmascarada, nunca el secreto completo


def mask(secret: str) -> str:
    return secret[:4] + "*" * 8


def check_line(
    text: str, commit: str, file: str, line_no: int, config: Config
) -> Iterator[Finding]:
    if IGNORE_MARKER in text:
        return
    for name, pattern in config.rules.items():
        match = pattern.search(text)
        if match and not config.value_allowed(match.group(0)):
            yield Finding(name, commit, file, line_no, mask(match.group(0)))


def scan_repo(repo: Path, config: Config) -> list[Finding]:
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
                # "+++ b/ruta" = archivo nuevo; "+++ /dev/null" = archivo borrado
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