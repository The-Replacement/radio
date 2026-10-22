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
import os
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
        for nro, linea in enumerate(fh, start=1):
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            partes = linea.split()
            if len(partes) < 2:
                print(
                    f"tokens.txt linea {nro}: se esperaba 'nombre token ...'",
                    file=sys.stderr,
                )
                return 1
            nombre, token = partes[0], partes[1]
            if len(partes) >= 3:
                cookie = partes[2]
            else:
                cookie = secrets.token_hex(32)  # formato viejo de 2 campos
                migrado = True
            vence = partes[3] if len(partes) >= 4 else ""
            # Se valida ANTES de cualquier escritura: un vencimiento mal
            # formado aborta limpio y no deja tokens.txt a medias.
            if vence:
                try:
                    date.fromisoformat(vence)
                except ValueError:
                    print(
                        f"tokens.txt linea {nro}: vencimiento inválido: {vence!r}"
                        " (usá YYYY-MM-DD)",
                        file=sys.stderr,
                    )
                    return 1
            filas.append([nombre, token, cookie, vence])

    if migrado:
        # Escritura atómica: tmp + os.replace. Si el proceso muere a mitad
        # de camino, tokens.txt no queda truncado.
        tmp = entrada + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(
                " ".join(x for x in (n, t, c, v) if x) + "\n" for n, t, c, v in filas
            )
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, entrada)

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
        "# Define el matcher @gate, el puente y el matcher @canje.",
        "#",
        f"# {len(vigentes)} credencial(es) vigente(s).",
    ]
    if vencidos:
        lineas.append(
            f"# {len(vencidos)} vencida(s), omitida(s) del matcher:"
            f" {', '.join(vencidos)}"
        )

    # El puente: token de la URL -> cookieid de esa persona.
    #
    # Sólo las vigentes. Si un token venció, no está en el map ni en @canje:
    # el link no canjea y el token tampoco abre el gate.
    puente = [f"\t{json.dumps(f[1])} {json.dumps(f[2])}" for f in vigentes]

    # @cam_ok: el token viajando en el CAMINO, para reproductores.
    #
    # /cam/<token>/index.m3u8 . El playlist apunta a archivos relativos, así
    # que el reproductor hereda el token en cada segmento y no hay que
    # reescribir nada. Con el token en el query eso NO funciona: el
    # reproductor pide /cam/seg123.m4s sin query y come 404 (verificado).
    #
    # {re.cam.1} lo captura el matcher @cam_path del Caddyfile, que se evalúa
    # ANTES. Si esta condición se evaluara primero, compararía contra "".
    cam = [clausula("{re.cam.1}", f[1], f[3]) for f in vigentes]

    lineas += [
        "",
        "# @gate: token (reproductores) o cookie (navegador), y ademas vigente.",
        "@gate {",
        f"\texpression `{o(tk + ck)}`",
        "}",
        "",
        "# El puente token -> cookieid, que usa @canje para dejar la cookie.",
        "#",
        "# POR QUE EXISTE: las dos columnas de tokens.txt son credenciales",
        "# DISTINTAS. Si el canje escribiera el token dentro de la cookie, el",
        "# @gate de arriba compararia esa cookie contra la columna cookieid y",
        "# no coincidiria nunca: TODA peticion posterior daria 404. El canje",
        "# no puede copiar, tiene que traducir.",
        "map {http.request.uri.query.t} {radio_cookie} {",
        *puente,
        "}",
        "",
        "# @canje: el link con ?t=... deja la cookie y la URL queda limpia.",
        "#",
        '# El guardia `{radio_cookie} != ""` no es decorativo. Si el map no',
        "# llegara a resolver, sin el guardia la cookie se setearia VACIA y el",
        "# oyente perderia la sesion en silencio. Con el guardia, el canje no",
        "# matchea, la peticion cae a @gate (que YA acepta el token en la URL)",
        "# y la radio sigue andando. Falla visible antes que sesion rota.",
        "@canje {",
        "\tpath /",
        "\texpression `(" + '{radio_cookie} != ""' + f" && ({o(tk)}))`",
        "}",
        "",
        "# @cam_ok: token en el CAMINO (/cam/<token>/index.m3u8), para VLC/mpv.",
        "#",
        "# El playlist apunta a archivos RELATIVOS: con el token en el camino,",
        "# el reproductor lo hereda solo en cada segmento. Con el token en el",
        "# query no funciona, porque los pedidos de segmento van sin query.",
        "#",
        "# {re.cam.1} lo captura @cam_path en el Caddyfile, que se evalua ANTES",
        "# que este matcher. El anidado de handles no es adorno: obliga al orden.",
        "@cam_ok {",
        f"\texpression `{o(cam)}`",
        "}",
    ]

    with open(salida, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lineas) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
