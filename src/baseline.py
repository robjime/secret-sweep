import json
from datetime import date
from pathlib import Path

from scanner import Finding


def _key(repo: str, finding: Finding) -> tuple:
    return (repo, finding.rule, finding.commit, finding.file, finding.line)


class Baseline:
    """Hallazgos ya revisados y aceptados. Nunca guarda el valor del secreto."""

    def __init__(self, path: Path, entries: dict | None = None) -> None:
        self.path = path
        self.entries: dict[tuple, dict] = entries or {}

    @classmethod
    def load(cls, path: Path) -> "Baseline":
        if not path.exists():
            return cls(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            entries = {
                (e["repo"], e["rule"], e["commit"], e["file"], int(e["line"])): e
                for e in data["accepted"]
            }
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ValueError(f"Línea base inválida en {path}: {error}") from error
        return cls(path, entries)

    def split(self, repo: str, findings: list[Finding]) -> tuple[list[Finding], int]:
        """Separa los hallazgos nuevos de los ya revisados. Devuelve (nuevos, nº de conocidos)."""
        new = [f for f in findings if _key(repo, f) not in self.entries]
        return new, len(findings) - len(new)

    def accept(self, repo: str, findings: list[Finding], reason: str) -> list[Finding]:
        added = []
        for f in findings:
            key = _key(repo, f)
            if key not in self.entries:
                self.entries[key] = {
                    "repo": repo,
                    "rule": f.rule,
                    "commit": f.commit,
                    "file": f.file,
                    "line": f.line,
                    "reason": reason,
                    "date": date.today().isoformat(),
                }
                added.append(f)
        return added

    def save(self) -> None:
        accepted = sorted(
            self.entries.values(),
            key=lambda e: (e["repo"], e["commit"], e["file"], e["line"]),
        )
        text = json.dumps({"version": 1, "accepted": accepted}, indent=2, ensure_ascii=False)
        self.path.write_text(text + "\n", encoding="utf-8", newline="\n")