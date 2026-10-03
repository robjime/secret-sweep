import re
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# Cada regla: nombre legible -> expresión regular
RULES: dict[str, re.Pattern] = {
    "AWS Access Key ID": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "Clave privada": re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"
    ),
    "Webhook de Discord": re.compile(
        r"https://discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"
    ),
    "Token de bot de Telegram": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "Contraseña o clave asignada": re.compile(
        r"""(?i)[\w-]*(?:password|passwd|pwd|secret|api[_-]?key|token)[\w-]*["']?\s*[:=]\s*["'][^"'\s]{6,}["']"""
    ),
}

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


def check_line(text: str, commit: str, file: str, line_no: int) -> Iterator[Finding]:
    for name, pattern in RULES.items():
        match = pattern.search(text)
        if match:
            yield Finding(name, commit, file, line_no, mask(match.group(0)))


def scan_repo(repo: Path) -> list[Finding]:
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
            elif line.startswith("@@"):
                match = HUNK_RE.match(line)
                if match:
                    line_no = int(match.group(1))
            elif line.startswith("+") and file:
                findings.extend(check_line(line[1:], commit, file, line_no))
                line_no += 1

    if proc.returncode != 0:
        raise RuntimeError("git devolvió un error al leer el historial")
    return findings