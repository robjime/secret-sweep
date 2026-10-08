"""Informe de análisis en Markdown.

Convierte los resultados del escáner en un documento legible. Del escáner solo se
usa la ubicación de cada hallazgo (regla, archivo, línea y commit): ni el valor del
secreto ni su versión enmascarada (`Finding.preview`) llegan al informe.

Ojo: los textos libres se copian tal cual (motivos de la línea base, mensajes de
error, rutas y nombres), así que no deben contener secretos.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from baseline import Baseline
from github_client import finding_url
from scanner import Finding


@dataclass
class RepoResult:
    """Resultado del análisis de un repositorio.

    Attributes:
        name: Nombre del repositorio.
        new: Hallazgos que no estaban en la línea base. Con `--accept`, son los que
            se acaban de aceptar.
        known: Cuántos hallazgos se omitieron por estar ya en la línea base.
        url: Enlace web del repositorio (vacío en un análisis local).
        error: Mensaje de error si el análisis falló, o None si fue bien.
    """

    name: str
    new: list[Finding] = field(default_factory=list)
    known: int = 0
    url: str = ""
    error: str | None = None


def _cell(text: object) -> str:
    """Neutraliza lo que rompe una tabla de Markdown: `|`, comillas invertidas y saltos.

    Escapa las barras verticales (`|` pasa a `\\|`), cambia las comillas invertidas
    por comillas simples (el texto suele ir dentro de un fragmento de código con
    comillas invertidas) y convierte los saltos de línea en espacios. No toca el
    resto de Markdown: un texto como `[a](http://b)` seguiría siendo un enlace.

    Args:
        text: Valor a escapar; se convierte a texto.

    Returns:
        El texto, apto para una celda de tabla.
    """
    return str(text).replace("|", "\\|").replace("`", "'").replace("\n", " ")


def _commit(finding_commit: str, url: str) -> str:
    """Formatea el commit como sus 7 primeros caracteres, con enlace si hay URL.

    Args:
        finding_commit: Hash completo del commit.
        url: Enlace al archivo en ese commit, o cadena vacía.
    """
    short = f"`{finding_commit[:7]}`"
    return f"[{short}]({url})" if url else short


def build_report(
    results: list[RepoResult],
    baseline: Baseline,
    mode: str,
    accepting: bool,
    generated_at: str,
) -> str:
    """Construye el informe completo en Markdown.

    Secciones, en orden: cabecera con el resumen general, tabla por repositorio,
    hallazgos nuevos (o aceptados en esta ejecución), repositorios que no se
    pudieron analizar (solo si hay alguno), hallazgos ya revisados de la línea base
    (solo si hay alguno, y solo de los repositorios de `results`) y un aviso final.

    No escribe en disco ni modifica sus argumentos. No usa `Finding.preview`.

    Args:
        results: Resultado de cada repositorio analizado.
        baseline: Línea base de la que salen los hallazgos ya revisados. Cada entrada
            debe tener `reason` y `date`.
        mode: Descripción del origen (por ejemplo, "Local (mi-repo)" o
            "GitHub (10 repositorios)").
        accepting: True si se ejecutó con `--accept`: cambia los títulos y estados.
        generated_at: Fecha y hora, ya formateadas como texto.

    Returns:
        El informe, como texto Markdown.

    Raises:
        KeyError: Si una entrada de la línea base no tiene `reason` o `date`.
    """
    errors = [r for r in results if r.error]
    total_new = sum(len(r.new) for r in results)
    total_known = sum(r.known for r in results)
    palabra = "aceptados en esta ejecución" if accepting else "nuevos"

    lines = [
        "# Informe de secret-sweep",
        "",
        f"- **Fecha:** {generated_at}",
        f"- **Modo:** {mode}",
        f"- **Repositorios analizados:** {len(results) - len(errors)} de {len(results)}",
        f"- **Hallazgos {palabra}:** {total_new}",
        f"- **Hallazgos ya revisados (línea base):** {total_known}",
        f"- **Repositorios con error:** {len(errors)}",
        "",
        "## Resumen por repositorio",
        "",
        "| Repositorio | Estado | Hallazgos | Ya revisados |",
        "|---|---|---:|---:|",
    ]
    for r in results:
        if r.error:
            estado = "ERROR"
        elif r.new:
            estado = "Aceptados" if accepting else "Con hallazgos"
        else:
            estado = "Limpio"
        lines.append(f"| {_cell(r.name)} | {estado} | {len(r.new)} | {r.known} |")

    lines += ["", f"## Hallazgos {palabra}", ""]
    if total_new:
        lines += ["| Repositorio | Regla | Ubicación | Commit |", "|---|---|---|---|"]
        for r in results:
            for f in r.new:
                ubicacion = f"`{_cell(f.file)}:{f.line}`"
                commit = _commit(f.commit, finding_url(r.url, f))
                lines.append(f"| {_cell(r.name)} | {_cell(f.rule)} | {ubicacion} | {commit} |")
    else:
        lines.append("Ninguno.")

    if errors:
        lines += ["", "## Repositorios que no se pudieron analizar", ""]
        lines += [f"- **{_cell(r.name)}**: {_cell(r.error)}" for r in errors]

    names = {r.name for r in results}
    accepted = sorted(
        (e for e in baseline.entries.values() if e["repo"] in names),
        key=lambda e: (e["repo"], e["commit"], e["file"], e["line"]),
    )
    if accepted:
        lines += [
            "",
            "## Hallazgos revisados (línea base)",
            "",
            "| Repositorio | Regla | Ubicación | Commit | Motivo | Fecha |",
            "|---|---|---|---|---|---|",
        ]
        for e in accepted:
            lines.append(
                f"| {_cell(e['repo'])} | {_cell(e['rule'])} | `{_cell(e['file'])}:{e['line']}` "
                f"| `{e['commit'][:7]}` | {_cell(e['reason'])} | {_cell(e['date'])} |"
            )

    lines += [
        "",
        "---",
        "",
        "*Generado por secret-sweep. No contiene valores de secretos, pero sí rutas y "
        "nombres de repositorios (también privados): revísalo antes de compartirlo.*",
        "",
    ]
    return "\n".join(lines)


def save_report(directory: Path, text: str, now: datetime) -> Path:
    """Guarda el informe en `informe-AAAAMMDD-HHMMSS.md` dentro de `directory`.

    Crea la carpeta si no existe y escribe en UTF-8 con saltos de línea LF. Si ya
    hay un informe con el mismo nombre (dos en el mismo segundo), lo sobrescribe.

    Args:
        directory: Carpeta de destino.
        text: Contenido del informe.
        now: Instante usado para dar nombre al archivo.

    Returns:
        La ruta del archivo (relativa si `directory` lo es).

    Raises:
        OSError: Si no se puede crear la carpeta o escribir el archivo.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / now.strftime("informe-%Y%m%d-%H%M%S.md")
    path.write_text(text, encoding="utf-8", newline="\n")
    return path