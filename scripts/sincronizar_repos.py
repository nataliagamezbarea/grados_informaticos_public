#!/usr/bin/env python3
"""
Sincroniza todas las ramas y commits desde MAESTRO hacia el repositorio PRIVADO
inyectando el token de autenticación en cada push.
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.error

SCHEMA = "grados-informaticos"
RAMAS_EXCLUIDAS = {"master", "main", "gh-pages"}

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
    # 1. Obtener token con permisos globales (SIEMPRE de configuracion_privada)
    gh_token = consultar_supabase("configuracion_privada", "gh_token")
    if not gh_token:
        gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    # 2. Obtener nombre del repositorio privado
    gh_repo = consultar_supabase("configuracion_privada", "gh_repo")
    if not gh_repo:
        gh_repo = os.environ.get("GH_REPO_PRIVADO", "nataliagamezbarea/grados_informaticos_privado")

    return gh_token, gh_repo

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

def main():
    token, repo_privado = obtener_credenciales_privado()
    if not token:
        print("[FATAL] No se encontró el token de GitHub (gh_token).")
        sys.exit(1)
    if not repo_privado:
        print("[FATAL] No se definió el repositorio privado (gh_repo).")
        sys.exit(1)

    # URL remota con autenticación obligatoria
    remote_auth = f"https://x-access-token:{token}@github.com/{repo_privado}.git"

    # Actualizar todas las referencias remotas
    ejecutar_cmd(["git", "fetch", "--all", "--prune"], check=False)

    ramas = obtener_ramas_locales()
    print(f"PRIVADO: master/main/gh-pages excluidas. Revisando {len(ramas)} ramas de MAESTRO.")

    for rama in ramas:
        commits = obtener_commits_rama(rama)
        if not commits:
            print(f"PRIVADO: Rama {rama} sin commits para sincronizar.")
            continue

        print(f"PRIVADO: {rama} -> publicando {len(commits)} commits UNO A UNO.")
        for i, commit in enumerate(commits, 1):
            print(f"PRIVADO: {rama} commit {i}/{len(commits)} -> {commit}")
            res_push = subprocess.run(
                ["git", "push", remote_auth, f"{commit}:refs/heads/{rama}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if res_push.returncode != 0:
                print(f"ERROR: {res_push.stderr.strip()}")
                sys.exit(1)

    print("[OK] Sincronización del repositorio privado completada con éxito.")

if __name__ == "__main__":
    main()