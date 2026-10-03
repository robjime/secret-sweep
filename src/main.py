import argparse
import sys
from pathlib import Path

from scanner import scan_repo


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Busca secretos en el historial completo de un repositorio Git."
    )
    parser.add_argument("repo", type=Path, help="ruta al repositorio local")
    args = parser.parse_args()

    try:
        findings = scan_repo(args.repo)
    except (ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    for f in findings:
        print(f"[{f.rule}] {f.file}:{f.line} (commit {f.commit[:7]}) -> {f.preview}")

    print(f"\n{len(findings)} posible(s) secreto(s) encontrado(s).")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())