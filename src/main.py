import argparse
import sys
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

from baseline import Baseline
from config import Config, load_config
from github_client import clone_or_update, get_token, list_repos
from notifier import Alert, Notifier, format_findings, get_notifier
from scanner import Finding, scan_repo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path(__file__).with_name("rules.yaml")
DEFAULT_WORKDIR = ROOT / "repos"
DEFAULT_BASELINE = ROOT / "baseline.json"
ENV_FILE = ROOT / ".env"


def format_finding(f: Finding, prefix: str = "") -> str:
    return f"{prefix}[{f.rule}] {f.file}:{f.line} (commit {f.commit[:7]}) -> {f.preview}"


def finding_url(html_url: str, f: Finding) -> str:
    """Enlace permanente a la línea exacta, en el commit donde apareció."""
    if not html_url:
        return ""
    return f"{html_url}/blob/{f.commit}/{quote(f.file)}#L{f.line}"


def status(count: int, known: int, accepting: bool) -> str:
    palabra = "aceptado(s)" if accepting else "nuevo(s)"
    texto = f"{count} hallazgo(s) {palabra}" if count else "limpio"
    return f"{texto}, {known} ya revisado(s)" if known else texto


def safe_send(notifier: Notifier, title: str, message: str, severity: str) -> None:
    """Envía un aviso sin que un fallo del propio aviso oculte los resultados."""
    try:
        notifier.send(title, message, severity)
    except RuntimeError as error:
        print(f"[AVISO] No se pudo enviar el aviso: {error}", file=sys.stderr)


def notify_findings(notifier: Notifier, alerts: list[Alert]) -> None:
    safe_send(
        notifier,
        f"secret-sweep: {len(alerts)} hallazgo(s) nuevo(s)",
        format_findings(alerts),
        "critical",
    )


def report_repo(
    name: str,
    findings: list[Finding],
    baseline: Baseline,
    reason: str | None,
    header: bool = True,
) -> tuple[list[Finding], int]:
    """Aplica la línea base a un repo, imprime lo que toca y devuelve (nuevos, nº de conocidos)."""
    accepting = reason is not None
    new, known = baseline.split(name, findings)
    if accepting:
        baseline.accept(name, new, reason)
    if header:
        print(f"{name}: {status(len(new), known, accepting)}")
    prefix = ("    " if header else "") + ("[aceptado] " if accepting else "")
    for f in new:
        print(format_finding(f, prefix))
    return new, known


def run_local(
    repo: Path,
    config: Config,
    baseline: Baseline,
    reason: str | None,
    notifier: Notifier | None = None,
) -> int:
    findings = scan_repo(repo, config)
    accepting = reason is not None
    name = repo.resolve().name
    new, known = report_repo(name, findings, baseline, reason, header=False)
    if accepting:
        baseline.save()
    print(f"\n{status(len(new), known, accepting)}.")

    if new and notifier and not accepting:
        notify_findings(notifier, [(name, f, "") for f in new])
    return 1 if new and not accepting else 0


def run_github(
    config: Config,
    workdir: Path,
    include_forks: bool,
    baseline: Baseline,
    reason: str | None,
    notifier: Notifier | None = None,
) -> int:
    token = get_token()
    repos = list_repos(token, include_forks)
    print(f"{len(repos)} repositorio(s) para analizar.\n")

    accepting = reason is not None
    alerts: list[Alert] = []
    failed: list[str] = []
    known_total = 0
    for repo in repos:
        try:
            path = clone_or_update(repo, workdir, token)
            findings = scan_repo(path, config)
        except (ValueError, RuntimeError) as error:
            failed.append(repo.name)
            print(f"[ERROR] {repo.name}: {error}", file=sys.stderr)
            continue
        new, known = report_repo(repo.name, findings, baseline, reason)
        known_total += known
        alerts += [(repo.name, f, finding_url(repo.html_url, f)) for f in new]

    if accepting:
        baseline.save()
    palabra = "aceptado(s)" if accepting else "nuevo(s)"
    print(
        f"\nResumen: {len(repos)} repo(s), {len(alerts)} {palabra}, "
        f"{known_total} ya revisado(s), {len(failed)} error(es)."
    )

    if notifier and not accepting:
        if alerts:
            notify_findings(notifier, alerts)
        elif failed:
            # Un análisis incompleto no puede pasar por "todo limpio": se avisa.
            safe_send(
                notifier,
                "secret-sweep: análisis incompleto",
                f"No se pudieron analizar {len(failed)} repositorio(s): " + ", ".join(failed),
                "warning",
            )

    if alerts and not accepting:
        return 1
    return 2 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Busca secretos en el historial completo de repositorios Git."
    )
    parser.add_argument("repo", type=Path, nargs="?", help="ruta a un repositorio local")
    parser.add_argument(
        "--github",
        action="store_true",
        help="escanea todos tus repositorios de GitHub (requiere GITHUB_TOKEN)",
    )
    parser.add_argument(
        "--include-forks", action="store_true", help="con --github, incluye también los forks"
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=DEFAULT_WORKDIR,
        help="carpeta donde se clonan los repos (por defecto: repos/)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="archivo YAML con reglas y exclusiones (por defecto: src/rules.yaml)",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="archivo JSON con los hallazgos ya revisados (por defecto: baseline.json)",
    )
    parser.add_argument(
        "--no-baseline",
        action="store_true",
        help="ignora la línea base y muestra todos los hallazgos",
    )
    parser.add_argument(
        "--accept",
        metavar="MOTIVO",
        help="acepta como revisados los hallazgos nuevos y los guarda en la línea base",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="avisa por Discord si hay hallazgos nuevos (requiere DISCORD_WEBHOOK_URL)",
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help="envía un aviso de prueba a Discord y termina",
    )
    args = parser.parse_args()

    if not args.test_notify and bool(args.repo) == args.github:
        parser.error("indica la ruta de un repositorio o usa --github (pero no ambos)")
    if args.accept is not None and args.no_baseline:
        parser.error("--accept no se puede combinar con --no-baseline")
    if args.accept is not None and not args.accept.strip():
        parser.error("--accept necesita un motivo")

    try:
        load_dotenv(ENV_FILE)
        if args.test_notify:
            get_notifier().send(
                "secret-sweep: prueba",
                "Si ves este mensaje, el webhook funciona correctamente.",
                "info",
            )
            print("Aviso de prueba enviado.")
            return 0

        # Se crea al principio: si falta el webhook, falla antes de un escaneo largo.
        notifier = get_notifier() if args.notify else None
        config = load_config(args.config)
        baseline = Baseline(args.baseline) if args.no_baseline else Baseline.load(args.baseline)
        reason = args.accept.strip() if args.accept is not None else None
        if args.github:
            return run_github(
                config, args.workdir, args.include_forks, baseline, reason, notifier
            )
        return run_local(args.repo, config, baseline, reason, notifier)
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())