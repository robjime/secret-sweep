import subprocess

from scanner import scan_repo


def git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo),
         "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
        check=True, capture_output=True,
    )


def test_detecta_secreto_borrado(tmp_path):
    git(tmp_path, "init")
    archivo = tmp_path / "config.py"

    archivo.write_text('AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "add config")

    archivo.write_text('AWS_KEY = "cambiar"\n', encoding="utf-8")
    git(tmp_path, "commit", "-am", "remove key")

    findings = scan_repo(tmp_path)

    assert any(f.rule == "AWS Access Key ID" for f in findings)
    assert all("AKIAIOSFODNN7EXAMPLE" not in f.preview for f in findings)