import argparse
import sys
from pathlib import Path

from config import load_config
from scanner import scan_repo

DEFAULT_CONFIG = Path(__file__).with_name("rules.yaml")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Busca secretos en el historial completo de un repositorio Git."
    )
    parser.add_argument("repo", type=Path, help="ruta al repositorio local")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="archivo YAML con reglas y exclusiones (por defecto: src/rules.yaml)",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        findings = scan_repo(args.repo, config)
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    for f in findings:
        print(f"[{f.rule}] {f.file}:{f.line} (commit {f.commit[:7]}) -> {f.preview}")

    print(f"\n{len(findings)} posible(s) secreto(s) encontrado(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())