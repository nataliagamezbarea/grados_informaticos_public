import base64
#!/usr/bin/env python3
"""
MAESTRO -> PRIVADO

Sincroniza el historial real commit-a-commit hacia el repositorio PRIVADO,
sin usar la API de GitHub para crear ramas (porque eso falla si el repo
está vacío). Aquí TODO se hace con git:

- Si la rama no existe → se crea con commit vacío usando git.
- Luego se publican todos los commits reales con `git push --force`.

Esto evita completamente el error 409 "Git Repository is empty".
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.error

SCHEMA = "grados-informaticos"
RAMAS_EXCLUIDAS = {"master", "main", "gh-pages"}


# --------------------------------------------------------------------------
# Credenciales desde Supabase (configuracion_privada)
# --------------------------------------------------------------------------


def git_env_with_token(token):
    """Environment for GitHub HTTPS using HTTP Basic auth.

    GitHub accepts the PAT/token as the password and x-access-token as the
    username. This avoids the 'Repository not found' failure caused by using
    Bearer auth for Git transport.
    """
    env = os.environ.copy()
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
    env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {auth}"
    return env

def obtener_env_supabase():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY", "")
    return url, key


def consultar_supabase(tabla, clave_buscar):
    url, key = obtener_env_supabase()
    if not url or not key:
        return None

    endpoint = f"{url}/rest/v1/{tabla}?clave=eq.{clave_buscar}&select=valor"
    req = urllib.request.Request(endpoint, headers={
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Accept-Profile": SCHEMA,
        "Content-Profile": SCHEMA
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data:
                valor = data[0].get("valor")
                return valor.strip() if isinstance(valor, str) else valor
    except Exception as e:
        print(f"[WARN] Error consultando Supabase ({tabla}.{clave_buscar}): {e}")
    return None


def obtener_credenciales_privado():
    gh_token = consultar_supabase("configuracion_privada", "gh_token")
    if not gh_token:
        gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    gh_repo = consultar_supabase("configuracion_privada", "gh_repo")
    if not gh_repo:
        gh_repo = os.environ.get("GH_REPO_PRIVADO", "nataliagamezbarea/grados_informaticos_privado")

    return gh_token, gh_repo


# --------------------------------------------------------------------------
# Git local (checkout completo de MAESTRO)
# --------------------------------------------------------------------------

def ejecutar_cmd(cmd, check=True):
    res = subprocess.run(cmd, env=git_env_with_token(gh_token), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and res.returncode != 0:
        print(f"ERROR: {res.stderr.strip()}")
        sys.exit(res.returncode)
    return res.stdout.strip()


def obtener_ramas_locales():
    salida = ejecutar_cmd(["git", "branch", "-r"])
    ramas = []
    for linea in salida.splitlines():
        linea = linea.strip()
        if "->" in linea:
            continue
        if linea.startswith("origin/"):
            nombre = linea.replace("origin/", "")
            if nombre not in RAMAS_EXCLUIDAS:
                ramas.append(nombre)
    return list(set(ramas))


def obtener_commits_rama(rama):
    salida = ejecutar_cmd(["git", "log", f"origin/{rama}", "--reverse", "--format=%H"])
    return [c.strip() for c in salida.splitlines() if c.strip()]


# --------------------------------------------------------------------------
# CREACIÓN DE RAMAS EN PRIVADO (solo git, sin API)
# --------------------------------------------------------------------------

def branch_exists(remote_auth, rama):
    res = subprocess.run(
        ["git", "ls-remote", "--heads", remote_auth, rama],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    , env=git_env_with_token(gh_token))
    return res.stdout.strip() != ""


def crear_rama_vacia(remote_auth, rama):
    print(f"[INFO] Creando rama vacía en PRIVADO: {rama}")

    ejecutar_cmd(["git", "checkout", "--orphan", f"temp_{rama}"])
    ejecutar_cmd(["git", "rm", "-rf", "."])
    ejecutar_cmd(["git", "commit", "--allow-empty", "-m", f"Crear rama {rama} vacía"])

    sha = ejecutar_cmd(["git", "rev-parse", "HEAD"])

    res = subprocess.run(
        ["git", "push", remote_auth, f"{sha}:refs/heads/{rama}"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    , env=git_env_with_token(gh_token))

    if res.returncode != 0:
        print(f"[FATAL] No se pudo crear la rama {rama} en el repo privado.")
        print(res.stderr)
        sys.exit(1)

    ejecutar_cmd(["git", "checkout", "-"])
    print(f"[OK] Rama {rama} creada en PRIVADO.")


def asegurar_rama(remote_auth, rama):
    if not branch_exists(remote_auth, rama):
        crear_rama_vacia(remote_auth, rama)


# --------------------------------------------------------------------------
# Publicación commit-a-commit
# --------------------------------------------------------------------------

def publicar_rama(remote_auth, token, repo, rama):
    asegurar_rama(remote_auth, rama)

    commits = obtener_commits_rama(rama)
    if not commits:
        print(f"PRIVADO: Rama {rama} sin commits para sincronizar.")
        return

    print(f"PRIVADO: {rama} -> publicando {len(commits)} commits UNO A UNO.")
    for i, commit in enumerate(commits, 1):
        print(f"PRIVADO: {rama} commit {i}/{len(commits)} -> {commit}")

        res_push = subprocess.run(
            ["git", "push", "--force", remote_auth, f"{commit}:refs/heads/{rama}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        , env=git_env_with_token(gh_token))

        if res_push.returncode != 0:
            print(f"ERROR: {res_push.stderr.strip()}")
            sys.exit(1)


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def verificar_acceso_repo(token, repo):
    """Consulta la API de GitHub para saber si el token puede ver el repo
    y así distinguir 'no existe' de 'sin permiso' de 'token invalido'."""
    api_url = f"https://api.github.com/repos/{repo}"
    req = urllib.request.Request(api_url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[DEBUG] La API confirma que el repo existe y es visible: {data.get('full_name')} (privado: {data.get('private')})")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"[DEBUG] La API de GitHub responde 404 para '{repo}': o el repo no existe con ese nombre exacto, o el token no tiene permiso para verlo.")
        elif e.code == 401:
            print(f"[DEBUG] La API de GitHub responde 401: el token es inválido o ha caducado.")
        elif e.code == 403:
            print(f"[DEBUG] La API de GitHub responde 403: el token no tiene permisos suficientes sobre este repo.")
        else:
            print(f"[DEBUG] La API de GitHub respondió con error {e.code}: {e.read().decode('utf-8', errors='ignore')}")
    except Exception as e:
        print(f"[DEBUG] Error al consultar la API de GitHub: {e}")


def configurar_identidad_git():
    ejecutar_cmd(["git", "config", "--global", "user.email", "actions@github.com"])
    ejecutar_cmd(["git", "config", "--global", "user.name", "GitHub Actions"])


def main():
    configurar_identidad_git()

    token, repo_privado = obtener_credenciales_privado()
    if not token:
        print("[FATAL] No se encontró el token de GitHub (gh_token).")
        sys.exit(1)
    if not repo_privado:
        print("[FATAL] No se definió el repositorio privado (gh_repo).")
        sys.exit(1)

    remote_auth = f"https://x-access-token:{token}@github.com/{repo_privado}.git"

    print(f"[DEBUG] Repo privado destino: '{repo_privado}' (longitud: {len(repo_privado)})")
    print(f"[DEBUG] Token presente: {'sí' if token else 'no'} (longitud: {len(token) if token else 0}, primeros 4 chars: {token[:4] if token else 'N/A'})")

    verificar_acceso_repo(token, repo_privado)

    ejecutar_cmd(["git", "fetch", "--all", "--prune"], check=False)

    ramas = obtener_ramas_locales()
    print(f"PRIVADO: master/main/gh-pages excluidas. Revisando {len(ramas)} ramas de MAESTRO.")

    for rama in ramas:
        publicar_rama(remote_auth, token, repo_privado, rama)

    print("[OK] Sincronización del repositorio privado completada con éxito.")


if __name__ == "__main__":
    main()
