"""Avisos sobre hallazgos nuevos: interfaz `Notifier` y envío por webhook de Discord.

La URL del webhook es un secreto: quien la tenga puede escribir en tu canal. Por eso:

- Se valida antes de usarla (ver `WEBHOOK_RE`): solo se aceptan URLs de Discord, para
  no enviar los avisos (nombres de repositorios y rutas de archivos) a otro servidor.
- Los errores de red de `requests` incluyen la URL en su mensaje; se ocultan con
  `from None`, de modo que no salen ni en el mensaje ni en el traceback que se imprime.
"""

import os
import re
from abc import ABC, abstractmethod

import requests

from scanner import Finding

# Formato estándar de un webhook de Discord. Es estricto a propósito: rechaza variantes
# válidas como `/api/v10/` o `?thread_id=...`, a cambio de no aceptar otros servidores.
WEBHOOK_RE = re.compile(
    r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+"
)
# Límite propio para la descripción del embed (Discord admite 4096): deja margen para
# la línea final "… y N más".
MAX_MESSAGE = 3500

Alert = tuple[str, Finding, str]  # (repositorio, hallazgo, enlace a la línea)


class Notifier(ABC):
    """Interfaz común para enviar avisos.

    El resto del programa solo conoce esta interfaz, así que añadir otro canal
    (Telegram, correo...) no obliga a tocarlo: basta con otra clase que implemente
    `send`.
    """

    @abstractmethod
    def send(self, title: str, message: str, severity: str = "info") -> None:
        """Envía un aviso.

        Args:
            title: Título corto.
            message: Cuerpo del aviso.
            severity: "info", "warning" o "critical".

        Raises:
            RuntimeError: Si no se pudo enviar. Las implementaciones deben usar solo
                esta excepción: es la única que captura `main.safe_send`.
        """
        ...


class DiscordNotifier(Notifier):
    """Envía avisos a un canal de Discord mediante un webhook.

    Cada aviso es un mensaje con formato (embed) cuyo borde cambia de color según la
    gravedad.

    Attributes:
        COLORS: Color del borde del mensaje para cada gravedad.
    """

    COLORS = {"info": 0x2ECC71, "warning": 0xE67E22, "critical": 0xE74C3C}

    def __init__(self, webhook_url: str, session=None) -> None:
        """Crea el cliente y comprueba el formato de la URL.

        Args:
            webhook_url: URL completa del webhook.
            session: Objeto con un método `post` como el de `requests.Session`; sirve
                para sustituirlo por uno falso en las pruebas.

        Raises:
            ValueError: Si la URL no tiene el formato estándar de un webhook de
                Discord. El mensaje no incluye la URL.
        """
        if not WEBHOOK_RE.fullmatch(webhook_url):
            # No se incluye la URL en el mensaje: es un secreto.
            raise ValueError("DISCORD_WEBHOOK_URL no tiene el formato de un webhook de Discord.")
        self._url = webhook_url
        self._session = session or requests.Session()

    def send(self, title: str, message: str, severity: str = "info") -> None:
        """Envía un aviso a Discord como un mensaje con formato (embed).

        El título se recorta a 256 caracteres y el cuerpo a 4000 (límites de Discord);
        el recorte es silencioso. Se desactiva el procesado de menciones (`@everyone`,
        `<@id>`...) para que ningún texto de un hallazgo pueda avisar a nadie. No
        reintenta si falla; el timeout es de 10 segundos.

        Args:
            title: Título del mensaje.
            message: Cuerpo (admite el Markdown de Discord).
            severity: "info", "warning" o "critical", que decide el color. Cualquier
                otro valor usa el de "info".

        Raises:
            RuntimeError: Si falla la conexión (el error original de `requests` se
                oculta, porque contiene la URL) o si Discord responde con un código
                que no sea 200 ni 204, incluido 429 (límite de avisos).
        """
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
    """Lee el webhook del entorno y devuelve un notificador listo para usar.

    No lee el archivo `.env`: `main.py` lo carga antes de llamar aquí. Se quitan los
    espacios y saltos de línea del principio y del final.

    Returns:
        Un `DiscordNotifier` configurado.

    Raises:
        ValueError: Si `DISCORD_WEBHOOK_URL` no existe, está vacía o no tiene el
            formato de un webhook de Discord.
    """
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise ValueError(
            "Falta DISCORD_WEBHOOK_URL. Crea un webhook en tu canal de Discord y "
            "guárdalo en el archivo .env (ver .env.example)."
        )
    return DiscordNotifier(url)


def format_findings(alerts: list[Alert]) -> str:
    """Agrupa los hallazgos por repositorio y les da formato Markdown para Discord.

    Por cada hallazgo incluye la regla, el archivo, la línea y el commit (con enlace
    si lo hay). No usa `Finding.preview`, así que ni siquiera el inicio de lo
    detectado sale de aquí. Si el texto pasara de `MAX_MESSAGE` caracteres, se corta y
    se añade una línea final con cuántos hallazgos quedaron fuera, de modo que cabe
    en el límite de Discord.

    Args:
        alerts: Tuplas `(repositorio, hallazgo, enlace)`. El enlace puede ser una
            cadena vacía.

    Returns:
        El texto para la descripción del embed (cadena vacía si no hay hallazgos).
    """
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