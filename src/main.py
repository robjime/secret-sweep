import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from baseline import Baseline
from config import Config, load_config
from github_client import clone_or_update, get_token, list_repos
from scanner import Finding, scan_repo

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = Path(__file__).with_name("rules.yaml")
DEFAULT_WORKDIR = ROOT / "repos"
DEFAULT_BASELINE = ROOT / "baseline.json"
ENV_FILE = ROOT / ".env"


def format_finding(f: Finding, prefix: str = "") -> str:
    return f"{prefix}[{f.rule}] {f.file}:{f.line} (commit {f.commit[:7]}) -> {f.preview}"


def status(count: int, known: int, accepting: bool) -> str:
    palabra = "aceptado(s)" if accepting else "nuevo(s)"
    texto = f"{count} hallazgo(s) {palabra}" if count else "limpio"
    return f"{texto}, {known} ya revisado(s)" if known else texto


def report_repo(
    name: str,
    findings: list[Finding],
    baseline: Baseline,
    reason: str | None,
    header: bool = True,
) -> tuple[int, int]:
    """Aplica la línea base a un repo, imprime lo que toca y devuelve (nuevos, conocidos)."""
    accepting = reason is not None
    new, known = baseline.split(name, findings)
    if accepting:
        baseline.accept(name, new, reason)
    if header:
        print(f"{name}: {status(len(new), known, accepting)}")
    prefix = ("    " if header else "") + ("[aceptado] " if accepting else "")
    for f in new:
        print(format_finding(f, prefix))
    return len(new), known


def run_local(repo: Path, config: Config, baseline: Baseline, reason: str | None) -> int:
    findings = scan_repo(repo, config)
    accepting = reason is not None
    new, known = report_repo(repo.resolve().name, findings, baseline, reason, header=False)
    if accepting:
        baseline.save()
    print(f"\n{status(new, known, accepting)}.")
    return 1 if new and not accepting else 0


def run_github(
    config: Config,
    workdir: Path,
    include_forks: bool,
    baseline: Baseline,
    reason: str | None,
) -> int:
    token = get_token()
    repos = list_repos(token, include_forks)
    print(f"{len(repos)} repositorio(s) para analizar.\n")

    accepting = reason is not None
    new_total = known_total = errors = 0
    for repo in repos:
        try:
            path = clone_or_update(repo, workdir, token)
            findings = scan_repo(path, config)
        except (ValueError, RuntimeError) as error:
            errors += 1
            print(f"[ERROR] {repo.name}: {error}", file=sys.stderr)
            continue
        new, known = report_repo(repo.name, findings, baseline, reason)
        new_total += new
        known_total += known

    if accepting:
        baseline.save()
    palabra = "aceptado(s)" if accepting else "nuevo(s)"
    print(
        f"\nResumen: {len(repos)} repo(s), {new_total} {palabra}, "
        f"{known_total} ya revisado(s), {errors} error(es)."
    )
    if new_total and not accepting:
        return 1
    return 2 if errors else 0


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
    args = parser.parse_args()

    if bool(args.repo) == args.github:
        parser.error("indica la ruta de un repositorio o usa --github (pero no ambos)")
    if args.accept is not None and args.no_baseline:
        parser.error("--accept no se puede combinar con --no-baseline")
    if args.accept is not None and not args.accept.strip():
        parser.error("--accept necesita un motivo")

    try:
        config = load_config(args.config)
        baseline = Baseline(args.baseline) if args.no_baseline else Baseline.load(args.baseline)
        reason = args.accept.strip() if args.accept is not None else None
        if args.github:
            load_dotenv(ENV_FILE)
            return run_github(config, args.workdir, args.include_forks, baseline, reason)
        return run_local(args.repo, config, baseline, reason)
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())