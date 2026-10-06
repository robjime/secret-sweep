from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from baseline import Baseline
from github_client import finding_url
from scanner import Finding


@dataclass
class RepoResult:
    name: str
    new: list[Finding] = field(default_factory=list)
    known: int = 0
    url: str = ""
    error: str | None = None


def _cell(text: object) -> str:
    """Escapa un texto para que no rompa una tabla de Markdown."""
    return str(text).replace("|", "\\|").replace("`", "'").replace("\n", " ")


def _commit(finding_commit: str, url: str) -> str:
    short = f"`{finding_commit[:7]}`"
    return f"[{short}]({url})" if url else short


def build_report(
    results: list[RepoResult],
    baseline: Baseline,
    mode: str,
    accepting: bool,
    generated_at: str,
) -> str:
    """Informe en Markdown. Nunca incluye valores de secretos, solo su ubicación."""
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
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / now.strftime("informe-%Y%m%d-%H%M%S.md")
    path.write_text(text, encoding="utf-8", newline="\n")
    return path