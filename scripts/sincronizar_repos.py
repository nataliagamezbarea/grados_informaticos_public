#!/usr/bin/env python3
"""
MAESTRO -> PRIVADO

Reutiliza de sync_common.py la MISMA función que usa el público para
asegurar que la rama existe en el destino (asegurar_rama: si no existe,
la crea por API con un commit vacío; main/master nunca se autocrean).

A diferencia del público, el CONTENIDO no se sincroniza vía API de
contenidos: aquí se envía el historial real, commit a commit, con
`git push --force`.

El --force es necesario porque el commit vacío que crea asegurar_rama()
no tiene relación de parentesco con el historial real de MAESTRO (es un
commit sin padres, con árbol vacío). Un `git push` normal del primer
commit real sobre esa rama sería rechazado por non-fast-forward, ya que
Git ve dos historias no relacionadas. Como el privado es un espejo del
historial de MAESTRO, forzar el push en cada commit es seguro: la
referencia del privado se deriva completamente de MAESTRO, no hay cambios
propios que se puedan perder.
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.error

from sync_common import asegurar_rama, RAMAS_SIN_AUTOCREAR

SCHEMA = "grados-informaticos"
RAMAS_EXCLUIDAS = {"master", "main", "gh-pages"}


# --------------------------------------------------------------------------
# Credenciales (Supabase configuracion_privada, con fallback a variables
# de entorno)
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
            if data and len(data) > 0:
                if len(data) > 1:
                    print(
                        f"[WARN] {tabla}.{clave_buscar} tiene {len(data)} filas "
                        f"duplicadas; se usará la primera devuelta por Supabase."
                    )
                valor = data[0].get("valor")
                if isinstance(valor, str):
                    valor_limpio = valor.strip()
                    if valor_limpio != valor:
                        print(
                            f"[WARN] {tabla}.{clave_buscar} tenía espacios/"
                            f"saltos de línea sobrantes; se han recortado."
                        )
                    return valor_limpio
                return valor
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
# Git local (sobre el checkout completo de MAESTRO)
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
# Publicación commit a commit
# --------------------------------------------------------------------------

def publicar_rama(remote_auth, token, repo, rama):
    # Igual que el público: asegura que la rama exista en el destino
    # (la crea por API con commit vacío si hace falta; main/master nunca
    # se autocrean, aunque de todas formas ya están excluidas antes).
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

