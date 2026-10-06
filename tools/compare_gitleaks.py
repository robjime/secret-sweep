"""Compara secret-sweep con Gitleaks sobre el mismo repositorio.

Uso:
    gitleaks git <repo> --report-format json --report-path gitleaks.json --redact
    python tools/compare_gitleaks.py <repo> gitleaks.json

Solo usa la ubicación de cada hallazgo (commit, archivo, línea) y el nombre de la regla.
Nunca lee ni imprime los campos con el valor del secreto (Secret, Match). Aun así, ejecuta
Gitleaks con --redact y borra el informe JSON al terminar.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import load_config  # noqa: E402
from scanner import scan_repo  # noqa: E402

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "src" / "rules.yaml"


def key(commit: str, file: str, line: int) -> tuple[str, str, int]:
    return (commit[:7], file.replace("\\", "/"), int(line))


def load_gitleaks(path: Path) -> dict[tuple, set[str]]:
    data = json.loads(path.read_text(encoding="utf-8")) or []
    found: dict[tuple, set[str]] = {}
    for item in data:
        if not item.get("Commit"):
            continue
        k = key(item["Commit"], item["File"], item["StartLine"])
        found.setdefault(k, set()).add(item["RuleID"])
    return found


def load_secret_sweep(repo: Path) -> dict[tuple, set[str]]:
    found: dict[tuple, set[str]] = {}
    for f in scan_repo(repo, load_config(DEFAULT_CONFIG)):
        found.setdefault(key(f.commit, f.file, f.line), set()).add(f.rule)
    return found


def describe(k: tuple, rules: set[str]) -> str:
    commit, file, line = k
    return f"- `{file}:{line}` (commit `{commit}`): {', '.join(sorted(rules))}"


def main() -> int:
    if len(sys.argv) != 3:
        print("Uso: python tools/compare_gitleaks.py <repo> <informe-gitleaks.json>")
        return 2
    repo, report = Path(sys.argv[1]), Path(sys.argv[2])
    ours = load_secret_sweep(repo)
    theirs = load_gitleaks(report)

    both = ours.keys() & theirs.keys()
    only_ours = ours.keys() - theirs.keys()
    only_theirs = theirs.keys() - ours.keys()

    print(f"## Comparación sobre `{repo.resolve().name}`\n")
    print("| | Hallazgos |")
    print("|---|---:|")
    print(f"| secret-sweep | {len(ours)} |")
    print(f"| Gitleaks | {len(theirs)} |")
    print(f"| En común | {len(both)} |")
    print(f"| Solo secret-sweep | {len(only_ours)} |")
    print(f"| Solo Gitleaks | {len(only_theirs)} |\n")

    for title, keys, source in (
        ("Solo secret-sweep", only_ours, ours),
        ("Solo Gitleaks", only_theirs, theirs),
    ):
        print(f"### {title}\n")
        if not keys:
            print("Ninguno.\n")
            continue
        for k in sorted(keys):
            print(describe(k, source[k]))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())