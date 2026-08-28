#!/usr/bin/env python3
"""
Sincroniza archivos en el repositorio público usando el token de configuracion_privada
(con permisos globales) o variables de entorno.
"""

import os
import sys
import json
import base64
import urllib.request
import urllib.error

SCHEMA = "grados-informaticos"

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
                return data[0].get("valor")
    except Exception as e:
        print(f"[WARN] Error consultando Supabase ({tabla}.{clave_buscar}): {e}")
    return None

def obtener_credenciales():
    # 1. Intentar obtener el token desde configuracion_privada (que tiene permisos completos)
    gh_token = consultar_supabase("configuracion_privada", "gh_token")
    if not gh_token:
        # Fallback a configuracion_publica o variable de entorno
        gh_token = consultar_supabase("configuracion_publica", "gh_token") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

    # 2. Obtener el nombre del repositorio público (probando ambas claves 'gh_repo' y 'gh_repo_invitados')
    gh_repo = consultar_supabase("configuracion_publica", "gh_repo")
    if not gh_repo:
        gh_repo = consultar_supabase("configuracion_publica", "gh_repo_invitados")
    if not gh_repo:
        gh_repo = os.environ.get("GH_REPO_PUBLICO", "nataliagamezbarea/grados_informaticos_publico")

    return gh_token, gh_repo

def api_github_request(url, token, data=None, method="GET"):
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GitHub-Actions-Sync"
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def sincronizar_archivo(repo, token, ruta_local):
    if not os.path.isfile(ruta_local):
        print(f"[SKIP] Archivo local no existe: {ruta_local}")
        return

    with open(ruta_local, "rb") as f:
        contenido_b64 = base64.b64encode(f.read()).decode("utf-8")

    api_url = f"https://api.github.com/repos/{repo}/contents/{ruta_local}"
    
    sha = None
    try:
        info_actual = api_github_request(api_url, token, method="GET")
        sha = info_actual.get("sha")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[ERROR] Error al verificar {ruta_local} en {repo}: {e}")
            raise

    payload = {
        "message": f"Sincronizar {ruta_local} desde maestro",
        "content": contenido_b64
    }
    if sha:
        payload["sha"] = sha

    try:
        api_github_request(api_url, token, data=payload, method="PUT")
        print(f"[OK] Sincronizado correctamente: {ruta_local} -> {repo}")
    except Exception as e:
        print(f"[ERROR] No se pudo subir {ruta_local}: {e}")
        raise

def main():
    token, repo = obtener_credenciales()
    if not token:
        print("[FATAL] No se encontró 'gh_token' ni en configuracion_privada ni en el entorno.")
        sys.exit(1)
    if not repo:
        print("[FATAL] No se pudo determinar el repositorio destino 'gh_repo'.")
        sys.exit(1)

    print(f"[INFO] Sincronizando con repositorio público: {repo}")

    archivos_str = os.environ.get("PUBLIC_CHANGED_FILES", "")
    archivos = [a.strip() for a in archivos_str.split(",") if a.strip()]

    if not archivos:
        print("[INFO] No hay archivos específicos para sincronizar.")
        return

    for archivo in archivos:
        sincronizar_archivo(repo, token, archivo)

if __name__ == "__main__":
    main()
