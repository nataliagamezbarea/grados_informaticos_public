#!/usr/bin/env python3
"""
MAESTRO -> PÚBLICO

Usa la lógica común de sync_common.py. Lo único propio de este script es
de dónde salen las credenciales (gh_repo de configuracion_publica, gh_token
SIEMPRE de configuracion_privada) y que ignora los cambios dentro de
.github/workflows/ (esos nunca se publican en el público).
"""

import os
import sys

from sync_common import asegurar_rama, normalize_path, sync_archivo, supabase_get


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
        sync_archivo(
            master_dir, token, repo, master_path, branch,
            crear_si_no_existe=False,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc)
        sys.exit(1)

