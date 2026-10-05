import subprocess

import pytest
import requests

from baseline import Baseline
from config import load_config
from github_client import RepoInfo
from main import main, run_github
from notifier import DiscordNotifier, Notifier, format_findings, get_notifier
from scanner import Finding

# Valores falsos construidos en tiempo de ejecución: no aparecen literales en el código.
FAKE_ID = "1" * 18
FAKE_SECRET = "x" * 40
FAKE_URL = "https://discord.com/api/webhooks/" + FAKE_ID + "/" + FAKE_SECRET
FAKE_TOKEN = "ghp_" + "x" * 36

RULES_YAML = r"""
rules:
  - name: AWS Access Key ID
    pattern: '\bAKIA[0-9A-Z]{16}\b'
"""


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeSession:
    def __init__(self, status_code=204, error=None):
        self.status_code = status_code
        self.error = error
        self.sent = []

    def post(self, url, json=None, timeout=None):
        if self.error:
            raise self.error
        self.sent.append(json)
        return FakeResponse(self.status_code)


class RecordingNotifier(Notifier):
    def __init__(self):
        self.sent = []

    def send(self, title, message, severity="info"):
        self.sent.append((title, message, severity))


class FailingNotifier(Notifier):
    def send(self, title, message, severity="info"):
        raise RuntimeError("Discord caído")


def finding(file="app.py", line=3, rule="Regla", commit="abc1234def"):
    return Finding(rule, commit, file, line, "AKIA********")


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        check=True, capture_output=True,
    )


def make_remote(tmp_path, nombre="remote", contenido='AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'):
    remote = tmp_path / nombre
    remote.mkdir()
    git(remote, "init")
    (remote / "a.txt").write_text(contenido, encoding="utf-8")
    git(remote, "add", ".")
    git(remote, "commit", "-m", "inicial")
    return remote


def make_config(tmp_path):
    ruta = tmp_path / "rules.yaml"
    ruta.write_text(RULES_YAML, encoding="utf-8")
    return load_config(ruta)


# ---------- DiscordNotifier ----------

def test_webhook_con_formato_invalido_no_se_acepta_ni_se_muestra():
    with pytest.raises(ValueError) as error:
        DiscordNotifier("https://example.com/hook/abc123")
    assert "example.com" not in str(error.value)


def test_envia_embed_con_color_y_sin_menciones():
    session = FakeSession()
    DiscordNotifier(FAKE_URL, session=session).send("Título", "Mensaje", "critical")

    payload = session.sent[0]
    assert payload["embeds"][0]["title"] == "Título"
    assert payload["embeds"][0]["color"] == 0xE74C3C
    assert payload["allowed_mentions"] == {"parse": []}


@pytest.mark.parametrize(
    "codigo, texto",
    [(429, "429"), (404, "404"), (500, "inesperado")],
)
def test_errores_http_dan_mensajes_claros(codigo, texto):
    notifier = DiscordNotifier(FAKE_URL, session=FakeSession(status_code=codigo))
    with pytest.raises(RuntimeError, match=texto):
        notifier.send("t", "m")


def test_error_de_red_no_filtra_la_url():
    error_red = requests.ConnectionError(f"Max retries exceeded with url: {FAKE_URL}")
    notifier = DiscordNotifier(FAKE_URL, session=FakeSession(error=error_red))

    with pytest.raises(RuntimeError) as error:
        notifier.send("t", "m")

    assert FAKE_SECRET not in str(error.value)
    assert FAKE_ID not in str(error.value)


def test_get_notifier_sin_variable(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    with pytest.raises(ValueError, match="DISCORD_WEBHOOK_URL"):
        get_notifier()


# ---------- Formato del mensaje ----------

def test_mensaje_agrupa_por_repo_y_no_incluye_el_valor():
    alertas = [
        ("repo-a", finding(file="x.py", line=7), "https://github.com/yo/repo-a/blob/abc/x.py#L7"),
        ("repo-b", finding(file="y.py", line=2), ""),
    ]

    mensaje = format_findings(alertas)

    assert "repo-a" in mensaje and "repo-b" in mensaje
    assert "`x.py:7`" in mensaje
    assert "https://github.com/yo/repo-a/blob/abc/x.py#L7" in mensaje
    assert "AKIA" not in mensaje  # el valor (ni su inicio) viaja nunca


def test_mensaje_largo_se_trunca_con_aviso():
    alertas = [("repo", finding(file=f"carpeta/archivo_{i}.py", line=i), "") for i in range(300)]

    mensaje = format_findings(alertas)

    assert len(mensaje) < 4000
    assert "más (consulta la salida de la consola)" in mensaje


# ---------- Integración con el escaneo ----------

def test_run_github_avisa_solo_de_lo_nuevo(tmp_path, monkeypatch):
    remote = make_remote(tmp_path)
    repo = RepoInfo("remote", str(remote), False, "https://github.com/yo/remote")
    monkeypatch.setenv("GITHUB_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr("main.list_repos", lambda token, include_forks=False: [repo])
    config = make_config(tmp_path)
    ruta_base = tmp_path / "baseline.json"
    notifier = RecordingNotifier()

    # 1. Hallazgo nuevo: se avisa, con enlace a la línea exacta
    codigo = run_github(config, tmp_path / "work", False, Baseline.load(ruta_base), None, notifier)
    assert codigo == 1
    titulo, mensaje, gravedad = notifier.sent[0]
    assert "1 hallazgo(s) nuevo(s)" in titulo and gravedad == "critical"
    assert "https://github.com/yo/remote/blob/" in mensaje and "#L1" in mensaje

    # 2. Se acepta: no se avisa
    notifier.sent.clear()
    run_github(config, tmp_path / "work", False, Baseline.load(ruta_base), "revisado", notifier)
    assert notifier.sent == []

    # 3. Ya revisado: silencio
    codigo = run_github(config, tmp_path / "work", False, Baseline.load(ruta_base), None, notifier)
    assert codigo == 0
    assert notifier.sent == []


def test_un_repo_que_falla_genera_aviso_de_analisis_incompleto(tmp_path, monkeypatch):
    roto = RepoInfo("roto", str(tmp_path / "no-existe"), False)
    monkeypatch.setenv("GITHUB_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr("main.list_repos", lambda token, include_forks=False: [roto])
    notifier = RecordingNotifier()

    codigo = run_github(
        make_config(tmp_path), tmp_path / "work", False, Baseline(tmp_path / "b.json"), None, notifier
    )

    assert codigo == 2
    titulo, mensaje, gravedad = notifier.sent[0]
    assert "incompleto" in titulo and "roto" in mensaje and gravedad == "warning"


def test_un_aviso_fallido_no_oculta_los_hallazgos(tmp_path, monkeypatch, capsys):
    remote = make_remote(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", FAKE_TOKEN)
    monkeypatch.setattr(
        "main.list_repos",
        lambda token, include_forks=False: [RepoInfo("remote", str(remote), False)],
    )

    codigo = run_github(
        make_config(tmp_path), tmp_path / "work", False, Baseline(tmp_path / "b.json"), None,
        FailingNotifier(),
    )

    assert codigo == 1  # el hallazgo sigue contando
    assert "[AVISO] No se pudo enviar el aviso: Discord caído" in capsys.readouterr().err


def test_test_notify_envia_un_mensaje_de_prueba(monkeypatch, capsys):
    notifier = RecordingNotifier()
    monkeypatch.setattr("main.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr("main.get_notifier", lambda: notifier)
    monkeypatch.setattr("sys.argv", ["main", "--test-notify"])

    assert main() == 0
    assert notifier.sent[0][0] == "secret-sweep: prueba"
    assert "Aviso de prueba enviado" in capsys.readouterr().out