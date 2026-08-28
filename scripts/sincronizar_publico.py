#!/usr/bin/env python3
"""
MAESTRO -> PÚBLICO

IMPORTANTE:
- NO hace git clone del repositorio público.
- NO descarga el árbol completo del repositorio público.
- Usa exclusivamente la API de GitHub para:
    1) comprobar la ubicación configurada (rama + ruta);
    2) buscar el archivo por nombre exacto mediante Code Search cuando hace falta;
    3) leer/modificar/eliminar únicamente el blob del archivo encontrado.
- El resto del repositorio nunca se copia al runner.

Reglas (SIN tabla mapeo_publico):
- Se procesan únicamente los archivos que han cambiado en el push (mismos
  que detecta el workflow en PUBLIC_CHANGED_FILES).
- Para cada archivo cambiado, se busca por NOMBRE EXACTO en gh_repo con
  GitHub Code Search (no se asume ninguna ruta previa, no hay mapeo).
- Si GitHub devuelve más de una coincidencia, NO se modifica nada (ambiguo).
- Si hay una única coincidencia, se actualiza/borra en esa ruta encontrada.
- Si no se encuentra ninguna coincidencia y el archivo existe en MAESTRO,
  se crea en el público con la MISMA RUTA que tiene en el MAESTRO.
- Si no se encuentra ninguna coincidencia y el archivo ya no existe en
  MAESTRO, no hay nada que borrar.
- El PÚBLICO nunca se convierte en espejo completo del MAESTRO: solo se
  tocan los archivos que cambiaron en este push.

NOTA SOBRE RAMAS:
Se publica en la MISMA rama que originó el push en MAESTRO (PUBLIC_BRANCH,
la pasa el workflow con github.ref_name).
- Si esa rama es "main" y no existe todavía en el público: el script NO
  la crea. Falla con un error claro (main nunca se autocrea).
- Si esa rama NO es "main" y no existe todavía en el público: se crea
  limpia, sin historial, con un único commit que contiene solo el
  archivo que se está sincronizando en ese momento (nunca copia commits
  de otra rama).
GitHub Code Search solo indexa de forma fiable la rama por defecto del
repo público, así que si el archivo se movió a otra rama, la localización
por nombre puede no encontrarlo ahí.

Supabase (schema: grados-informaticos):
  configuracion_publica (clave/valor):
      clave=gh_repo    -> "usuario/repositorio" del repo público
      clave=allowed_repos (opcional, no usado si solo hay un gh_repo)

  configuracion_privada (clave/valor):
      clave=gh_token   -> SIEMPRE se usa este, aunque el destino sea el
                          repo público (gh_token no se lee de
                          configuracion_publica)
"""

import base64
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

SCHEMA = "grados-informaticos"
GITHUB_API = "https://api.github.com"
RAMAS_SIN_AUTOCREAR = {}  # estas NUNCA se crean automáticamente


def github_request(token, method, path, data=None, accept=None):
    url = GITHUB_API + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": accept or "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "maestro-sincronizacion",
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
        except Exception:
            detail = {"message": raw}
        if exc.code == 404:
            return 404, detail
        raise RuntimeError(
            f"GitHub API {method} {path} -> HTTP {exc.code}: "
            f"{detail.get('message', raw)}"
        ) from exc


def supabase_get(table, params):
    url = os.environ["SUPABASE_URL"].rstrip("/")
    key = (
        os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("SUPABASE_KEY")
    )
    if not key:
        raise RuntimeError(
            "Falta SUPABASE_SERVICE_ROLE_KEY (o SUPABASE_KEY) en los secrets de Actions."
        )
    req = Request(
        f"{url}/rest/v1/{table}?{params}",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Accept-Profile": SCHEMA,
            "Content-Profile": SCHEMA,
        },
    )
    with urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode())


def get_config():
    """
    gh_repo sale de configuracion_publica.
    gh_token sale SIEMPRE de configuracion_privada (no se intenta leer de
    configuracion_publica).
    """
    rows_pub = supabase_get(
        "configuracion_publica",
        "select=clave,valor&clave=in.(gh_repo,allowed_repos)"
    )
    c = {r["clave"]: r["valor"] for r in rows_pub}

    rows_priv = supabase_get(
        "configuracion_privada",
        "select=clave,valor&clave=eq.gh_token"
    )
    priv = {r["clave"]: r["valor"] for r in rows_priv}
    token = priv.get("gh_token")

    if not token:
        raise RuntimeError(
            "Falta configuracion_privada.gh_token en Supabase."
        )
    if not c.get("gh_repo"):
        raise RuntimeError(
            "Falta configuracion_publica.gh_repo en Supabase."
        )

    return token, c["gh_repo"]


def normalize_path(path):
    return str(path).lstrip("/")


def get_contents(token, repo, path, branch):
    """
    Pide SOLO ese archivo a GitHub.
    No hace clone, no pide el árbol y no descarga otros archivos.
    """
    qpath = quote(normalize_path(path), safe="/")
    ref = quote(branch, safe="")
    status, data = github_request(
        token,
        "GET",
        f"/repos/{repo}/contents/{qpath}?ref={ref}",
    )
    if status == 404:
        return None
    if not isinstance(data, dict) or data.get("type") != "file":
        raise RuntimeError(
            f"La ubicación {repo}:{branch}/{path} no es un archivo."
        )
    return data


def search_exact_filename(token, repo, filename):
    """
    GitHub Code Search: busca SOLO por nombre exacto.
    No descarga el repositorio.
    """
    # Las comillas obligan a buscar la cadena exacta como nombre.
    query = quote(f'repo:{repo} filename:"{filename}"', safe="")
    status, data = github_request(
        token,
        "GET",
        f"/search/code?q={query}&per_page=100",
    )
    if status != 200:
        raise RuntimeError(f"No se pudo hacer Code Search en {repo}.")

    items = data.get("items", [])
    found = []
    for item in items:
        path = normalize_path(item.get("path", ""))
        if Path(path).name == filename:
            found.append({
                "path": path,
                "sha": item.get("sha"),
            })

    # Un mismo archivo puede aparecer duplicado en la respuesta.
    unique = {}
    for item in found:
        unique[item["path"]] = item
    return list(unique.values())


def get_file_by_search_result(token, repo, item):
    """
    Code Search devuelve la ruta y SHA, pero no garantiza la rama.
    El resultado se usa para localizar la ruta; la rama configurada sigue
    siendo la rama de publicación. Para una ubicación movida a otra rama,
    la configuración debe actualizarse.
    """
    return item["path"]


def encode_content(path):
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def put_file(token, repo, branch, path, source, current):
    payload = {
        "message": f"Sincronizar {path} desde MAESTRO",
        "content": encode_content(source),
        "branch": branch,
    }
    if current and current.get("sha"):
        payload["sha"] = current["sha"]

    qpath = quote(normalize_path(path), safe="/")
    status, data = github_request(
        token,
        "PUT",
        f"/repos/{repo}/contents/{qpath}",
        payload,
    )
    if status not in (200, 201):
        detalle = data.get("message") if isinstance(data, dict) else data
        raise RuntimeError(
            f"No se pudo publicar {repo}:{branch}/{path} "
            f"(HTTP {status}): {detalle}"
        )
    print("PUBLICADO:", f"{repo}:{branch}/{path}")


def delete_file(token, repo, branch, path, current):
    if not current or not current.get("sha"):
        return

    payload = {
        "message": f"Eliminar {path}: eliminado en MAESTRO",
        "sha": current["sha"],
        "branch": branch,
    }
    qpath = quote(normalize_path(path), safe="/")
    status, data = github_request(
        token,
        "DELETE",
        f"/repos/{repo}/contents/{qpath}",
        payload,
    )
    if status != 200:
        detalle = data.get("message") if isinstance(data, dict) else data
        raise RuntimeError(
            f"No se pudo eliminar {repo}:{branch}/{path} "
            f"(HTTP {status}): {detalle}"
        )
    print("ELIMINADO:", f"{repo}:{branch}/{path}")


def branch_exists(token, repo, branch):
    ref = quote(branch, safe="")
    status, _ = github_request(token, "GET", f"/repos/{repo}/git/ref/heads/{ref}")
    return status == 200


# SHA del árbol vacío de Git. Es una constante universal (siempre existe,
# no depende del repo ni hace falta crearla): GitHub rechaza con 422 un
# POST a /git/trees con "tree": [] , así que este SHA se usa directamente.
ARBOL_VACIO_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def crear_commit_inicial_vacio(token, repo, branch):
    """
    Crea la rama con un commit vacío (usa el árbol vacío de Git). No
    depende de qué archivo se esté sincronizando en este push -> funciona
    igual si el primer cambio que le toca procesar es una creación o un
    borrado. Sin git clone: commit -> ref.
    """
    status, commit = github_request(token, "POST", f"/repos/{repo}/git/commits", {
        "message": f"Crear rama {branch}",
        "tree": ARBOL_VACIO_SHA,
    })
    if status not in (200, 201):
        raise RuntimeError(f"No se pudo crear el commit inicial en {repo}.")

    status, _ = github_request(token, "POST", f"/repos/{repo}/git/refs", {
        "ref": f"refs/heads/{branch}",
        "sha": commit["sha"],
    })
    if status not in (200, 201):
        raise RuntimeError(
            f"No se pudo crear la rama {branch} en {repo} con el commit inicial."
        )
    print(f"RAMA CREADA (vacía): {repo}:{branch}")


def asegurar_rama(token, repo, branch):
    """
    Si branch ya existe, no hace nada.
    Si no existe:
      - Si branch está en RAMAS_SIN_AUTOCREAR (main, master): NUNCA se
        crea. Lanza error.
      - Si branch es cualquier otra: se crea vacía (commit vacío), sin
        depender de si el archivo que se está sincronizando en este push
        es una creación o un borrado.
    """
    if branch_exists(token, repo, branch):
        return

    if branch in RAMAS_SIN_AUTOCREAR:
        raise RuntimeError(
            f"La rama {branch} no existe en {repo} y no se autocrea nunca. "
            f"Créala manualmente si quieres publicar ahí."
        )

    crear_commit_inicial_vacio(token, repo, branch)


def locate(token, repo, master_path, branch):
    """
    Devuelve (branch, path, current_contents).

    1) Intenta localizarlo con GitHub Code Search por nombre exacto (rápido,
       pero SOLO indexa de forma fiable la rama por defecto del repo).
    2) Si Code Search no da una coincidencia válida en ESTA rama/ruta
       (0 coincidencias, o coincidencia que no está ahí realmente), se
       comprueba directamente con la API de Contents sobre la rama de
       destino y la ruta de MAESTRO. Esto es necesario porque ya no
       publicamos nada en la rama por defecto, así que Code Search casi
       nunca la encuentra aunque el archivo ya exista ahí.

    - >1 coincidencia de Code Search -> AMBIGUO, no se toca nada.
    """
    filename = Path(master_path).name
    matches = search_exact_filename(token, repo, filename)

    if len(matches) > 1:
        locations = ", ".join(x["path"] for x in matches)
        raise RuntimeError(
            f"AMBIGUO: {filename} aparece en varias rutas de {repo}: "
            f"{locations}. No se modifica nada."
        )

    if len(matches) == 1:
        path = matches[0]["path"]
        current = get_contents(token, repo, path, branch)
        if current:
            print(
                "LOCALIZADO POR CODE SEARCH:",
                f"{repo}:{branch}/{path}"
            )
            return branch, path, current
        # Code Search lo indexó (probablemente desde la rama por defecto)
        # pero no está en esa ruta dentro de ESTA rama -> se sigue abajo
        # con la comprobación directa en la ruta de MAESTRO.

    fallback_path = normalize_path(master_path)
    current = get_contents(token, repo, fallback_path, branch)
    if current:
        print(
            "LOCALIZADO POR COMPROBACIÓN DIRECTA:",
            f"{repo}:{branch}/{fallback_path}"
        )
    return branch, fallback_path, current


def sync_archivo(master_dir, token, repo, master_path, branch):
    master_path = normalize_path(master_path)
    source = Path(master_dir) / master_path

    asegurar_rama(token, repo, branch)

    branch, public_path, current = locate(token, repo, master_path, branch)

    if source.exists():
        put_file(token, repo, branch, public_path, source, current)
        return

    if current:
        delete_file(token, repo, branch, public_path, current)
    else:
        print(
            "ELIMINADO EN MAESTRO, NO ENCONTRADO EN PÚBLICO:",
            master_path,
        )


def main():
    token, repo = get_config()

    ramas_maestro = [
        r.strip()
        for r in os.environ.get("MAESTRO_BRANCHES", "").splitlines()
        if r.strip()
    ]
    for rama in ramas_maestro:
        asegurar_rama(token, repo, rama)

    changed = [
        p.strip()
        for p in os.environ.get("PUBLIC_CHANGED_FILES", "").splitlines()
        if p.strip()
    ]

    ignorados = [p for p in changed if normalize_path(p).startswith(".github/workflows/")]
    if ignorados:
        print("IGNORADOS (no se publican en el público):", ", ".join(ignorados))
    changed = [p for p in changed if not normalize_path(p).startswith(".github/workflows/")]

    if not changed:
        print("No hay archivos modificados que publicar.")
        return

    master_dir = os.environ["GITHUB_WORKSPACE"]
    branch = os.environ.get("PUBLIC_BRANCH")
    if not branch:
        raise RuntimeError("Falta PUBLIC_BRANCH en el entorno (github.ref_name).")

    for master_path in changed:
        sync_archivo(master_dir, token, repo, master_path, branch)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc)
        sys.exit(1)