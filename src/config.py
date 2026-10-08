"""Carga y validación de la configuración: reglas de detección y exclusiones.

Lee un archivo YAML (con `yaml.safe_load`, que no ejecuta código), compila las
expresiones regulares una sola vez y devuelve un objeto `Config`.

La validación es deliberadamente estricta: una configuración con tipos o valores
incorrectos se rechaza con `ValueError` antes de analizar nada. Una exclusión mal
escrita (por ejemplo, un texto donde se esperaba una lista) podría ignorar secretos
en silencio, y es preferible no arrancar a analizar con exclusiones que no son las
que se querían.
"""

import re
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Config:
    """Reglas de detección y exclusiones, ya compiladas.

    No se pueden reasignar sus atributos (`frozen=True`), pero el diccionario `rules`
    en sí sigue siendo modificable: la inmutabilidad es superficial.

    Attributes:
        rules: Nombre de cada regla y su expresión regular compilada.
        allow_paths: Patrones glob de rutas que se ignoran por completo. Se comparan
            con la ruta relativa al repositorio, con `/`; el `*` también cruza
            carpetas (`tests/*` incluye `tests/a/b.py`). En Windows no distinguen
            mayúsculas.
        allow_patterns: Expresiones regulares que se buscan dentro del texto
            detectado (no de la línea entera) para ignorar falsos positivos.
    """

    rules: dict[str, re.Pattern]
    allow_paths: tuple[str, ...] = ()
    allow_patterns: tuple[re.Pattern, ...] = ()

    def path_allowed(self, file: str) -> bool:
        """Indica si la ruta coincide con alguna exclusión de `allow_paths`.

        Args:
            file: Ruta del archivo, relativa al repositorio y con `/`.
        """
        return any(fnmatch(file, pattern) for pattern in self.allow_paths)

    def value_allowed(self, text: str) -> bool:
        """Indica si alguna exclusión de `allow_patterns` aparece en el texto.

        Usa `search`, así que basta con que coincida una parte del texto.

        Args:
            text: Texto detectado por una regla.
        """
        return any(p.search(text) for p in self.allow_patterns)


def _compile(pattern: str, where: str) -> re.Pattern:
    """Compila una expresión regular y rechaza las que no sirven.

    Se rechazan las expresiones inválidas y las que coinciden con un texto vacío
    (`''`, `.*`, `a*`...): con `search`, estas coinciden con cualquier texto. Como
    regla marcaría todas las líneas; como exclusión ignoraría todos los hallazgos.

    Args:
        pattern: Expresión regular a compilar.
        where: Dónde se usa (por ejemplo, "Regla 'AWS'"), para el mensaje de error.
            No debe contener el valor de la expresión.

    Returns:
        La expresión compilada.

    Raises:
        ValueError: Si la expresión no es válida o coincide con un texto vacío.
    """
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise ValueError(f"{where}: expresión regular inválida ({error})") from error
    if compiled.search(""):
        raise ValueError(f"{where}: la expresión coincide con cualquier texto")
    return compiled


def _string_list(value: object, where: str, path: Path) -> list[str]:
    """Comprueba que un valor del YAML es una lista de textos no vacíos.

    Args:
        value: Valor leído del YAML. `None` (clave vacía o ausente) cuenta como lista
            vacía.
        where: Nombre de la clave, para el mensaje de error.
        path: Archivo de configuración, para el mensaje de error.

    Returns:
        La lista de textos.

    Raises:
        ValueError: Si no es una lista, o algún elemento no es un texto o está vacío.
            El mensaje indica la posición del elemento, no su valor.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{path}: '{where}' debe ser una lista")
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{path}: '{where}' solo admite textos no vacíos (elemento {i})")
    return value


def load_config(path: Path) -> Config:
    """Lee un archivo YAML de configuración, lo valida y compila sus reglas.

    Lee de disco. Cualquier incumplimiento de la forma esperada se convierte en
    `ValueError` con un mensaje claro, para que el programa principal los trate todos
    igual. Los mensajes indican qué falla y dónde, nunca el valor de una exclusión.

    Args:
        path: Archivo YAML de configuración.

    Returns:
        La configuración lista para usar.

    Raises:
        ValueError: Si el archivo no se puede leer o no es un YAML válido; si `rules`
            falta, está vacía o no es una lista; si una regla no es un diccionario con
            `name` y `pattern` de tipo texto, o su nombre está repetido; si
            `allowlist` no es un diccionario o sus `paths` y `patterns` no son listas
            de textos no vacíos; o si alguna expresión regular no es válida o
            coincide con cualquier texto, o algún patrón de ruta coincide con todos
            los archivos.
    """
    try:
        with path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except OSError as error:
        raise ValueError(f"No se pudo leer {path}: {error}") from error
    except yaml.YAMLError as error:
        raise ValueError(f"YAML inválido en {path}: {error}") from error

    rules_data = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules_data, list) or not rules_data:
        raise ValueError(f"{path}: falta la sección 'rules' o está vacía (debe ser una lista)")

    rules: dict[str, re.Pattern] = {}
    for entry in rules_data:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("pattern"), str)
        ):
            raise ValueError(
                f"{path}: cada regla debe ser un diccionario con 'name' y 'pattern' de tipo texto"
            )
        name = entry["name"]
        if name in rules:
            raise ValueError(f"{path}: la regla '{name}' está repetida")
        rules[name] = _compile(entry["pattern"], f"Regla '{name}'")

    allow = data.get("allowlist") or {}
    if not isinstance(allow, dict):
        raise ValueError(f"{path}: 'allowlist' debe ser un diccionario")
    paths = _string_list(allow.get("paths"), "allowlist.paths", path)
    patterns = _string_list(allow.get("patterns"), "allowlist.patterns", path)

    for i, glob in enumerate(paths):
        if fnmatch("", glob):
            raise ValueError(
                f"{path}: 'allowlist.paths' (elemento {i}) coincide con todos los archivos"
            )

    return Config(
        rules=rules,
        allow_paths=tuple(paths),
        allow_patterns=tuple(
            _compile(p, f"allowlist.patterns (elemento {i})") for i, p in enumerate(patterns)
        ),
    )