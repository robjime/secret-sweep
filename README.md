# secret-sweep

Herramienta de línea de comandos que busca **secretos expuestos** (claves de API, tokens, contraseñas, claves privadas) en el **historial completo** de un repositorio Git, incluidos los que ya fueron borrados del código actual.

> **Estado:** en desarrollo (fase 1 completada: escáner local). Ver [hoja de ruta](#hoja-de-ruta).

## ¿Por qué existe?

Cuando se sube un secreto a Git y se borra en el commit siguiente, **sigue estando en el historial**. Cualquiera que clone un repositorio público puede recuperarlo, y existen bots que rastrean GitHub buscando justo eso. Esta herramienta revisa todos los commits de todas las ramas para detectar esos casos antes de que lo haga otra persona.

## Qué hace

- Recorre el historial con `git log --all -p` y analiza solo las líneas **añadidas** en cada commit.
- Aplica reglas basadas en expresiones regulares (AWS, tokens de GitHub, claves privadas, webhooks de Discord, tokens de bots de Telegram, contraseñas asignadas en código).
- Indica regla, archivo, línea y commit de cada hallazgo.
- **Nunca muestra el secreto completo**: solo los primeros caracteres, para no crear una segunda fuga en la terminal o en los logs.
- Lee el historial en *streaming*, así que funciona con repositorios grandes sin disparar el consumo de memoria.

## Requisitos

- Python 3.10 o superior
- Git instalado y disponible en el `PATH`

## Instalación (Windows, PowerShell)

```powershell
git clone https://github.com/robjime/secret-sweep.git
cd secret-sweep
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install pytest
```

## Uso

```powershell
python src\main.py C:\ruta\al\repositorio
```

Ejemplo de salida:

```
[AWS Access Key ID] config.py:12 (commit 3f1f204) -> AKIA********
[Contraseña o clave asignada] settings.py:7 (commit 8a9b2c3) -> DB_P********

2 posible(s) secreto(s) encontrado(s).
```

### Códigos de salida

| Código | Significado |
|---|---|
| `0` | Repositorio limpio |
| `1` | Se encontraron posibles secretos |
| `2` | Error (ruta inválida, fallo de Git...) |

Esto permite integrarlo en scripts o en un pipeline de CI: si el código no es `0`, el proceso puede detenerse.

## Pruebas

```powershell
pytest
```

Las pruebas crean repositorios temporales con claves **falsas** (la clave de ejemplo de la documentación de AWS) y comprueban que se detectan, incluso cuando ya fueron borradas en un commit posterior.

## Limitaciones conocidas

- **Falsos positivos:** las reglas son heurísticas. Por ejemplo, `TOKEN_FILE = "token.json"` puede marcarse como contraseña aunque sea solo un nombre de archivo. La fase 2 añade una lista de exclusiones.
- **Falsos negativos:** un secreto con un formato que ninguna regla contempla no se detectará.
- Solo se analizan líneas añadidas en texto; no se inspeccionan archivos binarios ni contenido comprimido.

Si se detecta un secreto real, borrarlo del último commit **no basta**: hay que **revocarlo y generar uno nuevo** en el servicio correspondiente, y solo después, si procede, limpiar el historial.

## Hoja de ruta

- [x] **Fase 1:** escáner local del historial de un repositorio
- [ ] **Fase 2:** reglas en YAML y lista de exclusiones (falsos positivos)
- [ ] **Fase 3:** conexión con la API de GitHub para listar, clonar y escanear todos los repositorios propios
- [ ] **Fase 4:** avisos por Discord (webhook)
- [ ] **Fase 5:** informe en Markdown y comparativa con herramientas como Gitleaks

## Aviso de uso

Usa esta herramienta únicamente sobre repositorios **propios** o sobre los que tengas autorización expresa para auditar.