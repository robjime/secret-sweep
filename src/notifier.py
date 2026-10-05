import os
import re
from abc import ABC, abstractmethod

import requests

from scanner import Finding

# Un webhook de Discord tiene siempre esta forma: solo se aceptan URLs así.
WEBHOOK_RE = re.compile(
    r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"
)
MAX_MESSAGE = 3500  # el límite de Discord es 4096 caracteres por embed

Alert = tuple[str, Finding, str]  # (repositorio, hallazgo, enlace a la línea)


class Notifier(ABC):
    """Interfaz común: el resto del programa solo sabe llamar a send()."""

    @abstractmethod
    def send(self, title: str, message: str, severity: str = "info") -> None:
        ...


class DiscordNotifier(Notifier):
    COLORS = {"info": 0x2ECC71, "warning": 0xE67E22, "critical": 0xE74C3C}

    def __init__(self, webhook_url: str, session=None) -> None:
        if not WEBHOOK_RE.fullmatch(webhook_url):
            # No se incluye la URL en el mensaje: es un secreto.
            raise ValueError("DISCORD_WEBHOOK_URL no tiene el formato de un webhook de Discord.")
        self._url = webhook_url
        self._session = session or requests.Session()

    def send(self, title: str, message: str, severity: str = "info") -> None:
        payload = {
            "embeds": [
                {
                    "title": title[:256],
                    "description": message[:4000],
                    "color": self.COLORS.get(severity, self.COLORS["info"]),
                }
            ],
            # Evita que un nombre de archivo con @everyone avise a todo el servidor.
            "allowed_mentions": {"parse": []},
        }
        try:
            response = self._session.post(self._url, json=payload, timeout=10)
        except requests.RequestException:
            # El mensaje original de requests incluye la URL (con el token): se descarta.
            raise RuntimeError("No se pudo conectar con Discord para enviar el aviso.") from None

        code = response.status_code
        if code in (200, 204):
            return
        if code == 429:
            raise RuntimeError("Discord limitó los avisos (429); inténtalo más tarde.")
        if code in (401, 403, 404):
            raise RuntimeError(f"Discord rechazó el webhook ({code}): ¿está borrado o mal copiado?")
        raise RuntimeError(f"Discord devolvió un error inesperado ({code}).")


def get_notifier() -> Notifier:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise ValueError(
            "Falta DISCORD_WEBHOOK_URL. Crea un webhook en tu canal de Discord y "
            "guárdalo en el archivo .env (ver .env.example)."
        )
    return DiscordNotifier(url)


def format_findings(alerts: list[Alert]) -> str:
    """Agrupa por repositorio. Solo incluye regla, archivo, línea y commit: nunca el valor."""
    by_repo: dict[str, list[tuple[Finding, str]]] = {}
    for repo, finding, url in alerts:
        by_repo.setdefault(repo, []).append((finding, url))

    rows: list[tuple[str, bool]] = []  # (texto, es_un_hallazgo)
    for repo, items in by_repo.items():
        rows.append((f"**`{repo}`** ({len(items)})", False))
        for finding, url in items:
            file = finding.file.replace("`", "'")
            commit = finding.commit[:7]
            ref = f"[{commit}]({url})" if url else f"`{commit}`"
            rows.append((f"• `{file}:{finding.line}` — {finding.rule} — {ref}", True))

    out: list[str] = []
    size = 0
    hidden = 0
    full = False
    for text, is_finding in rows:
        if not full and size + len(text) + 1 > MAX_MESSAGE:
            full = True
        if full:
            hidden += is_finding
            continue
        out.append(text)
        size += len(text) + 1
    if hidden:
        out.append(f"… y {hidden} más (consulta la salida de la consola).")
    return "\n".join(out)