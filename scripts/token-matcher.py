#!/usr/bin/env python3
"""Genera caddy/tokens.caddy a partir de secrets/tokens.txt.

Formato de tokens.txt, una linea por persona:

    nombre token cookieid [YYYY-MM-DD]

El cuarto campo es opcional: sin el, la credencial no vence nunca.

Por que las fechas se comparan como int(unix) y no como texto: en CEL los
placeholders de Caddy llegan como *strings*, asi que `{time.now.unix} < 123`
falla con "no such overload" (string contra int). `int(...)` lo convierte.
Verificado en un contenedor antes de escribir esto.

Los tokens ya vencidos NO se emiten: se los omite del matcher para que el
archivo no crezca con credenciales muertas. No se borran de tokens.txt.
"""

import json
import secrets
import sys
from datetime import date, datetime, timedelta


def fin_de_dia(iso: str) -> int:
    """Unix del inicio del dia siguiente.

    O sea: el token vale TODO el dia indicado. Se calcula en la hora local
    de quien corre el generador (el timestamp es absoluto, asi que despues
    no importa la zona del contenedor).
    """
    d = date.fromisoformat(iso)
    siguiente = datetime.combine(d + timedelta(days=1), datetime.min.time())
    return int(siguiente.astimezone().timestamp())


def clausula(campo: str, valor: str, vence: str) -> str:
    """Una condicion CEL: la credencial coincide Y (si corresponde) no vencio.

    Ojo con las llaves: en una f-string `{{` produce una sola `{`. Hay que
    escribir `{{` para que salga `{time.now.unix}` y no `{{time.now.unix}}`,
    que no compila en CEL.
    """
    if not vence:
        return f"({campo} == {json.dumps(valor)})"
    return (
        f"({campo} == {json.dumps(valor)}"
        f" && int({{time.now.unix}}) < {fin_de_dia(vence)})"
    )


def o(xs: list[str]) -> str:
    return " || ".join(xs) if xs else "false"


def main() -> int:
    if len(sys.argv) != 3:
        print("uso: token-matcher.py <tokens.txt> <tokens.caddy>", file=sys.stderr)
        return 2
    entrada, salida = sys.argv[1], sys.argv[2]

    filas: list[list[str]] = []
    migrado = False
    with open(entrada, encoding="utf-8") as fh:
        for linea in fh:
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            partes = linea.split()
            nombre, token = partes[0], partes[1]
            if len(partes) >= 3:
                cookie = partes[2]
            else:
                cookie = secrets.token_hex(32)  # formato viejo de 2 campos
                migrado = True
            vence = partes[3] if len(partes) >= 4 else ""
            filas.append([nombre, token, cookie, vence])

    if migrado:
        with open(entrada, "w", encoding="utf-8") as fh:
            fh.writelines(
                " ".join(x for x in (n, t, c, v) if x) + "\n" for n, t, c, v in filas
            )

    hoy = date.today()
    vigentes: list[list[str]] = []
    vencidos: list[str] = []
    for fila in filas:
        if fila[3] and date.fromisoformat(fila[3]) < hoy:
            vencidos.append(fila[0])
        else:
            vigentes.append(fila)

    tk = [clausula("{http.request.uri.query.t}", f[1], f[3]) for f in vigentes]
    ck = [clausula("{http.request.cookie.radio}", f[2], f[3]) for f in vigentes]

    lineas = [
        "# GENERADO POR scripts/token.sh - NO EDITAR A MANO.",
        "# Define los matchers @gate y @canje que usa el Caddyfile.",
        "#",
        f"# {len(vigentes)} credencial(es) vigente(s).",
    ]
    if vencidos:
        lineas.append(
            f"# {len(vencidos)} vencida(s), omitida(s) del matcher:"
            f" {', '.join(vencidos)}"
        )
    lineas += [
        "",
        "# @gate: token (reproductores) o cookie (navegador), y ademas vigente.",
        "@gate {",
        f"\texpression `{o(tk + ck)}`",
        "}",
        "",
        "# @canje: el link con ?t=... se vuelve cookie y la URL queda limpia.",
        "@canje {",
        "\tpath /",
        f"\texpression `{o(tk)}`",
        "}",
    ]

    with open(salida, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lineas) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
