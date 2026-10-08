"""Línea base de hallazgos ya revisados.

Una línea base permite que el escáner avise solo de lo nuevo: los hallazgos que una
persona ya revisó y aceptó se identifican por una huella (repo, regla, commit,
archivo y línea) y dejan de mostrarse. Nunca se guarda el valor del secreto.
"""

import json
from datetime import date
from pathlib import Path

from scanner import Finding


def _key(repo: str, finding: Finding) -> tuple:
    """Devuelve la huella de un hallazgo: (repo, regla, commit, archivo, línea).

    Es estable porque un commit no cambia: la misma línea en el mismo commit
    siempre produce la misma huella.
    """
    return (repo, finding.rule, finding.commit, finding.file, finding.line)


class Baseline:
    """Hallazgos revisados y aceptados, con su motivo y fecha.

    Solo guarda la ubicación del hallazgo (repo, regla, commit, archivo y línea) y
    el motivo y la fecha de la revisión. Nunca guarda el valor del secreto, ni
    siquiera enmascarado.

    Los cambios se hacen en memoria: hay que llamar a `save()` para guardarlos.

    Attributes:
        path: Archivo JSON donde se guarda la línea base.
        entries: Hallazgos aceptados, indexados por su huella (ver `_key`).
    """

    def __init__(self, path: Path, entries: dict | None = None) -> None:
        """Crea una línea base, vacía salvo que se indiquen `entries`.

        Args:
            path: Archivo JSON asociado a la línea base.
            entries: Hallazgos ya aceptados, indexados por su huella.
        """
        self.path = path
        self.entries: dict[tuple, dict] = entries or {}

    @classmethod
    def load(cls, path: Path) -> "Baseline":
        """Carga la línea base desde un archivo JSON.

        Args:
            path: Archivo a leer. Si no existe, se devuelve una línea base vacía.

        Returns:
            La línea base con los hallazgos del archivo.

        Raises:
            ValueError: Si el archivo no se puede leer, no es un JSON válido o
                le faltan campos obligatorios.
        """
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
        """Separa los hallazgos nuevos de los ya revisados.

        Args:
            repo: Nombre del repositorio analizado. Forma parte de la huella:
                aceptar un hallazgo en un repo no lo oculta en otro.
            findings: Hallazgos detectados en ese repositorio.

        Returns:
            Una tupla `(nuevos, conocidos)`: la lista de hallazgos que no están en
            la línea base y el número de los que sí estaban.
        """
        new = [f for f in findings if _key(repo, f) not in self.entries]
        return new, len(findings) - len(new)

    def accept(self, repo: str, findings: list[Finding], reason: str) -> list[Finding]:
        """Marca hallazgos como revisados y aceptados, con su motivo y fecha.

        Solo modifica la memoria: llama a `save()` para guardar el cambio. Los
        hallazgos que ya estaban aceptados se ignoran, sin sobrescribir su motivo.

        Args:
            repo: Nombre del repositorio.
            findings: Hallazgos a aceptar.
            reason: Por qué se aceptan (por ejemplo, "credencial de demo, solo local").

        Returns:
            Los hallazgos que se añadieron, es decir, los que no estaban ya aceptados.
        """
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
        """Guarda la línea base en su archivo JSON, sobrescribiéndolo.

        Las entradas se ordenan por repo, commit, archivo y línea para que el
        resultado sea siempre el mismo con los mismos datos (útil si algún día se
        versiona o se compara el archivo). Se escribe en UTF-8 con saltos de línea LF.

        Raises:
            OSError: Si no se puede escribir el archivo.
        """
        accepted = sorted(
            self.entries.values(),
            key=lambda e: (e["repo"], e["commit"], e["file"], e["line"]),
        )
        text = json.dumps({"version": 1, "accepted": accepted}, indent=2, ensure_ascii=False)
        self.path.write_text(text + "\n", encoding="utf-8", newline="\n")