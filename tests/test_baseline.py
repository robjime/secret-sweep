import json
import subprocess

import pytest

from baseline import Baseline
from config import load_config
from main import run_local
from scanner import Finding

RULES_YAML = r"""
rules:
  - name: AWS Access Key ID
    pattern: '\bAKIA[0-9A-Z]{16}\b'
"""


def finding(commit="abc1234", file="app.py", line=3, rule="Regla"):
    return Finding(rule, commit, file, line, "AKIA********")


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        check=True, capture_output=True,
    )


def commit_file(repo, name, content):
    (repo / name).write_text(content, encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", f"cambios en {name}")


# ---------- La clase Baseline ----------

def test_split_separa_nuevos_y_conocidos(tmp_path):
    baseline = Baseline(tmp_path / "baseline.json")
    viejo, nuevo = finding(line=3), finding(line=9)
    baseline.accept("mi-repo", [viejo], "revisado")

    nuevos, conocidos = baseline.split("mi-repo", [viejo, nuevo])

    assert nuevos == [nuevo]
    assert conocidos == 1


def test_aceptar_en_un_repo_no_oculta_el_mismo_hallazgo_en_otro(tmp_path):
    baseline = Baseline(tmp_path / "baseline.json")
    baseline.accept("repo-a", [finding()], "revisado")

    nuevos, conocidos = baseline.split("repo-b", [finding()])

    assert len(nuevos) == 1 and conocidos == 0


def test_guardar_y_cargar(tmp_path):
    ruta = tmp_path / "baseline.json"
    baseline = Baseline(ruta)
    baseline.accept("mi-repo", [finding()], "falso positivo en un docstring")
    baseline.save()

    cargada = Baseline.load(ruta)
    nuevos, conocidos = cargada.split("mi-repo", [finding()])

    assert nuevos == [] and conocidos == 1
    contenido = json.loads(ruta.read_text(encoding="utf-8"))
    entrada = contenido["accepted"][0]
    assert entrada["reason"] == "falso positivo en un docstring"
    assert "preview" not in entrada  # nunca se guarda nada del valor


def test_aceptar_dos_veces_no_duplica(tmp_path):
    baseline = Baseline(tmp_path / "baseline.json")
    baseline.accept("mi-repo", [finding()], "primera vez")

    añadidos = baseline.accept("mi-repo", [finding()], "segunda vez")

    assert añadidos == []
    assert len(baseline.entries) == 1


def test_linea_base_invalida(tmp_path):
    ruta = tmp_path / "baseline.json"
    ruta.write_text("esto no es json", encoding="utf-8")

    with pytest.raises(ValueError, match="Línea base"):
        Baseline.load(ruta)


def test_linea_base_inexistente_es_vacia(tmp_path):
    assert Baseline.load(tmp_path / "no-existe.json").entries == {}


# ---------- Flujo completo ----------

def test_flujo_completo_en_local(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    reglas = tmp_path / "rules.yaml"
    reglas.write_text(RULES_YAML, encoding="utf-8")
    config = load_config(reglas)
    ruta = tmp_path / "baseline.json"

    commit_file(repo, "a.py", 'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n')

    # 1. Primer escaneo: el hallazgo es nuevo
    assert run_local(repo, config, Baseline.load(ruta), None) == 1

    # 2. Se acepta con un motivo: queda guardado y el código de salida es 0
    assert run_local(repo, config, Baseline.load(ruta), "clave de ejemplo") == 0
    assert ruta.exists()

    # 3. Se vuelve a escanear: ya no es nuevo
    capsys.readouterr()
    assert run_local(repo, config, Baseline.load(ruta), None) == 0
    assert "1 ya revisado(s)" in capsys.readouterr().out

    # 4. Aparece un secreto distinto (construido en tiempo de ejecución): vuelve a saltar
    otra = "AKIA" + "IOSFODNN7EXAMPL2"
    commit_file(repo, "b.py", f'AWS_KEY = "{otra}"\n')
    assert run_local(repo, config, Baseline.load(ruta), None) == 1