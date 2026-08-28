#!/usr/bin/env python3
"""
MAESTRO -> PRIVADO

Usa EXACTAMENTE la misma lógica de ramas que el público (sync_common.py):
- Si la rama no existe y NO es main/master → se crea por API con commit vacío.
- Si es main/master y no existe → error (no se autocrean nunca).

Luego, a diferencia del público, el contenido NO se sincroniza por API:
se envía el historial real commit-a-commit con `git push --force`.
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.error

# 🔥 IMPORTA SOLO LO NECESARIO DEL PÚBLICO
from sync_common import asegurar_rama, RAMAS_SIN_AUTOCREAR

SCHEMA = "grados-informaticos"
RAMAS_EXCLUIDAS = {"master", "main", "gh-pages"}


# --------------------------------------------------------------------------
# Credenciales desde Supabase (configuracion_privada)
# --------------------------------------------------------------------------

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
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
# Publicación commit-a-commit
# --------------------------------------------------------------------------

def publicar_rama(remote_auth, token, repo, rama):
    # 🔥 Igual que el público: asegurar que la rama exista (API GitHub)
    asegurar_rama(token, repo, rama)

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
        )

        if res_push.returncode != 0:
            print(f"ERROR: {res_push.stderr.strip()}")
            sys.exit(1)


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    token, repo_privado = obtener_credenciales_privado()
    if not token:
        print("[FATAL] No se encontró el token de GitHub (gh_token).")
        sys.exit(1)
    if not repo_privado:
        print("[FATAL] No se definió el repositorio privado (gh_repo).")
        sys.exit(1)

    remote_auth = f"https://x-access-token:{token}@github.com/{repo_privado}.git"

    ejecutar_cmd(["git", "fetch", "--all", "--prune"], check=False)

    ramas = obtener_ramas_locales()
    print(f"PRIVADO: master/main/gh-pages excluidas. Revisando {len(ramas)} ramas de MAESTRO.")

    for rama in ramas:
        publicar_rama(remote_auth, token, repo_privado, rama)

    print("[OK] Sincronización del repositorio privado completada con éxito.")


if __name__ == "__main__":
    main()
