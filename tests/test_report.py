import subprocess
from datetime import datetime

from baseline import Baseline
from main import main
from report import RepoResult, build_report, save_report
from scanner import Finding

RULES_YAML = r"""
rules:
  - name: AWS Access Key ID
    pattern: '\bAKIA[0-9A-Z]{16}\b'
"""


def finding(file="app.py", line=3, rule="Contraseña o clave asignada", commit="abc1234def"):
    return Finding(rule, commit, file, line, "AKIA********")


def informe(results, baseline=None, accepting=False):
    baseline = baseline or Baseline(None)
    return build_report(results, baseline, "GitHub (2 repositorios)", accepting, "2026-10-06 12:00")


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        check=True, capture_output=True,
    )


def test_informe_con_hallazgos_y_enlaces():
    resultado = RepoResult(
        "mi-repo", [finding(file="src/a.py", line=7)], known=0, url="https://github.com/yo/mi-repo"
    )

    texto = informe([resultado])

    assert "# Informe de secret-sweep" in texto
    assert "| mi-repo | Con hallazgos | 1 | 0 |" in texto
    assert "`src/a.py:7`" in texto
    assert "https://github.com/yo/mi-repo/blob/abc1234def/src/a.py#L7" in texto


def test_informe_limpio_dice_ninguno():
    texto = informe([RepoResult("mi-repo", [], known=2)])

    assert "Ninguno." in texto
    assert "| mi-repo | Limpio | 0 | 2 |" in texto


def test_informe_no_incluye_ningun_valor():
    texto = informe([RepoResult("mi-repo", [finding()], url="https://github.com/yo/mi-repo")])

    assert "AKIA" not in texto  # ni siquiera el inicio de lo detectado


def test_informe_escapa_caracteres_que_rompen_tablas():
    baseline = Baseline(None)
    baseline.accept("mi-repo", [finding(file="a|b.py")], "motivo con | barra")

    texto = informe([RepoResult("mi-repo", [finding(file="a|b.py")])], baseline)

    assert "a\\|b.py" in texto
    assert "motivo con \\| barra" in texto


def test_informe_incluye_errores():
    texto = informe([RepoResult("roto", error="no se pudo clonar")])

    assert "| roto | ERROR | 0 | 0 |" in texto
    assert "## Repositorios que no se pudieron analizar" in texto
    assert "no se pudo clonar" in texto


def test_informe_lista_la_linea_base_solo_de_los_repos_analizados():
    baseline = Baseline(None)
    baseline.accept("mi-repo", [finding()], "practica universitaria")
    baseline.accept("otro-repo", [finding()], "no deberia salir")

    texto = informe([RepoResult("mi-repo", [], known=1)], baseline)

    assert "practica universitaria" in texto
    assert "no deberia salir" not in texto


def test_informe_en_modo_aceptar():
    texto = informe([RepoResult("mi-repo", [finding()])], accepting=True)

    assert "Hallazgos aceptados en esta ejecución" in texto
    assert "| mi-repo | Aceptados | 1 | 0 |" in texto


def test_guardar_informe_crea_la_carpeta(tmp_path):
    ruta = save_report(tmp_path / "reports", "# hola\n", datetime(2026, 10, 6, 12, 30, 5))

    assert ruta.name == "informe-20261006-123005.md"
    assert ruta.read_text(encoding="utf-8") == "# hola\n"


def test_cli_genera_informe(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    (repo / "a.py").write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "inicial")
    reglas = tmp_path / "rules.yaml"
    reglas.write_text(RULES_YAML, encoding="utf-8")
    carpeta = tmp_path / "informes"

    monkeypatch.setattr("main.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "sys.argv",
        ["main", str(repo), "--config", str(reglas), "--baseline", str(tmp_path / "b.json"),
         "--report", "--report-dir", str(carpeta)],
    )

    assert main() == 1
    archivos = list(carpeta.glob("informe-*.md"))
    assert len(archivos) == 1
    contenido = archivos[0].read_text(encoding="utf-8")
    assert "`a.py:1`" in contenido and "AWS Access Key ID" in contenido
    assert "Informe guardado en:" in capsys.readouterr().out