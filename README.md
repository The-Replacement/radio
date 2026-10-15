# Radio privada con token

Una radio que existe para un círculo cerrado. Sin token, no hay radio.

- **Liquidsoap** reproduce `./music` en orden aleatorio.
- **Icecast** sirve el stream — y **no se anuncia en ningún directorio público**.
- **Caddy** es la única puerta: sin token válido responde 404, no 401. No le confirma a nadie que acá hay algo.

Todo open source, todo en un `docker compose up`.

---

## Arranque

```bash
cd ~/Work/radio

# 1. Configuración (genera secretos aleatorios)
cp .env.example .env
sed -i "s|^ICECAST_SOURCE_PASSWORD=.*|ICECAST_SOURCE_PASSWORD=$(openssl rand -hex 24)|" .env
sed -i "s|^ICECAST_RELAY_PASSWORD=.*|ICECAST_RELAY_PASSWORD=$(openssl rand -hex 24)|"   .env
sed -i "s|^ICECAST_ADMIN_PASSWORD=.*|ICECAST_ADMIN_PASSWORD=$(openssl rand -hex 24)|"   .env

# 2. Permisos de los scripts
chmod +x scripts/*.sh

# 3. Levantar
docker compose up -d --build

# 4. Darte acceso a vos
./scripts/token.sh add yo

# 5. Verificar que la puerta cierre bien
./scripts/smoke.sh
```

El paso 4 te imprime el link completo. Ese link es la llave.

---

## Dar y sacar acceso

```bash
./scripts/token.sh add juan     # le da un token propio a juan
./scripts/token.sh ls           # quién tiene acceso
./scripts/token.sh rm juan      # le corta el acceso
```

**Un token por persona.** Es la razón de hacerlo así y no con una sola
contraseña compartida: si alguien filtra el link, le cortás el acceso
a esa persona y no le cambiás el link a todas las demás.

---

## Agregar música

Copiá los archivos a `./music/`. Entran solos, sin reiniciar nada.

---

## Cómo funciona la puerta

```
                 SIN token                    CON token
                     │                            │
                     ▼                            ▼
   oyente ──────► Caddy :443 ─────────────► Caddy :443
                     │                            │  (rewrite descarta el ?t=)
                     ▼                            ▼
                  404                       Icecast:8000
                                                   │
                                            (solo red Docker)
                                                   ▲
                                             Liquidsoap
                                                   ▲
                                              ./music
```

Tres capas, y cada una hace algo distinto:

1. **Caddy valida el token** contra `caddy/tokens.caddy` (generado).
2. **`rewrite` borra el `?t=` del query** antes de proxear: el token nunca
   llega a Icecast, así que no puede aparecer en sus logs.
3. **Icecast no publica puertos.** Está en la red interna de Docker. Aunque
   alguien encuentre el contenedor, no lo alcanza desde afuera.

Y la radio **no se anuncia en directorios** (`<directory>` ausente en
`icecast.xml` + `<public>0</public>`). No aparece en dir.xiph.org ni en
ningún listado de radios.

---

## Qué tan privado es esto, de verdad

Con honestidad, porque es tu decisión y necesitás el dato real:

**Lo que sí resuelve**
- Nadie llega sin token.
- No aparece en buscadores (noindex), ni en directorios de radios.
- El contenido va cifrado (TLS).
- Un token individual se revoca sin afectar a los demás.

**Lo que NO resuelve**
- **Caddy registra el token en el access log** (`?t=…` queda en el log de
  requests). Ver "Endurecer" abajo.
- El token viaja en la URL: si alguien comparte el link o lo pega en un
  chat sin cifrar, se filtra. Es el costo de la simplicidad.
- No oculta tu IP ni tu proveedor. Tu VPS sabe que esto existe.

**Endurecer (en orden de costo/beneficio)**

1. **Cookie en vez de query** — el token se canjea una vez por una cookie
   `HttpOnly`, y después no viaja más en la URL ni queda en los logs.
   Requiere un endpoint de login chiquito.
2. **Tokens con vencimiento** — `nombre token fecha`. Se revoca por tiempo.
3. **Un solo dispositivo por token** — límite de conexiones simultáneas.
4. **Audio sin costuras** — `crossfade`, jingles, `request.dynamic` para
   pedir temas por chat.

Pedí cualquiera de estas y lo agregamos.

---

## Si lo querés publicar en internet

En `.env`, cambiá:

```
SITE_ADDRESS=radio.daza.ar          # dónde sirve Caddy
PUBLIC_URL=https://radio.daza.ar    # la URL que le pasás a la gente
```

Y descomentá `email {$ACME_EMAIL}` en `caddy/Caddyfile` (ahí sí sirve el
`ACME_EMAIL` para los avisos de vencimiento del certificado).

En Cloudflare poné el registro **en gris (DNS only)**, nunca en naranja.
La regla de CDN de Cloudflare restringe el contenido no alojado en
Cloudflare, y un stream de audio cae justo ahí.

Caddy saca el certificado solito.

---

## Problemas comunes

**El stream entrega 0 bytes**
```bash
docker compose logs -f liquidsoap
```
Buscá `Connecting to icecast:8000`. Si no aparece, el encoder del `radio.liq`
no está disponible en la imagen. Probá cambiar `%mp3(...)` por:
```liquidsoap
%ffmpeg(format="mp3", %audio(codec="libmp3lame", bitrate=128))
```

**Icecast no arranca**
```bash
docker compose logs icecast
```
Casi siempre es un `ICECAST_*_PASSWORD` vacío en `.env`.

**Con token válido igual da 404**
Caddy no recargó. `./scripts/token.sh add yo` lo hace solo; si no:
```bash
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
```

**No suena en el iPhone**
Es la excepción a "MP3 lo reproduce todo" solo si cambiás el encoder a
Opus o Vorbis. Con MP3 128 debería andar en cualquier lado.

**Caddy arranca y se muere sin decir por qué**
Mirá primero si el bloque global tiene un token suelto. Esto es un error
real que nos pasó construyendo esto:

```
unrecognized global option: tu@email.com
```

Causa: `{$ACME_EMAIL:}` solo en el bloque global. Caddy lo lee como el
nombre de una opción. Tiene que ser `email {$ACME_EMAIL}`, y solo si el
valor no está vacío. Ver `caddy/Caddyfile`.

**`caddy reload` falla con "connection refused" en :2019**
Alguien puso `admin off`. Eso apaga la API de reload. Dejala activa: está
en localhost *dentro* del contenedor y el compose no publica el 2019.

**Avisos de Icecast que son inofensivos**
- `<hostname> not configured, using default value "localhost"` — Icecast
  avisa así cuando el hostname vale `localhost`. Es exactamente lo que
  queremos (solo lo alcanza Caddy). El aviso es por el YP, que no usamos.
- `Unsupported or legacy stream type: "audio/mpeg"` — Icecast no tiene un
  handler especializado para MP3 y cae al genérico. El audio sale igual.

**Los logs**
Icecast y Caddy escriben a stdout/stderr a propósito, para que `docker
compose logs` los muestre. No los mandes a archivo: si los redirigís,
te quedás ciego justo cuando necesitás ver el error.

---

## Archivos

```
radio/
├── docker-compose.yml        los tres servicios
├── .env.example              plantilla de configuración
├── icecast/
│   ├── Dockerfile            alpine + icecast (sin imagen de terceros)
│   ├── entrypoint.sh         inyecta los secretos desde el entorno
│   └── icecast.xml           sin <directory>: no nos anunciamos
├── liquidsoap/
│   └── radio.liq             la fuente: playlist → icecast
├── caddy/
│   ├── Caddyfile             la puerta
│   └── tokens.caddy          GENERADO por token.sh
├── web/index.html            el reproductor
├── music/                    tu música
├── scripts/
│   ├── token.sh              alta / baja / listado de accesos
│   └── smoke.sh              6 verificaciones, incluye "sin token no entra"
└── secrets/tokens.txt        QUIÉN tiene acceso. chmod 600.
```

---

## Contexto legal

Ver `PLAN-LEGAL-AR.md`. Resumen: con música libre (CC, Banco del INAMU,
producción propia) esto es **$0 y en regla**. Con música comercial,
corresponde licencia de SADAIC y CAPIF SGC.
