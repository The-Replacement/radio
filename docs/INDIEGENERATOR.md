# indiegenerator ↔ radio

Integración del generador de música (`indiegenerator`) con el stack de radio.

## Qué hace

El servicio `indiegen` (ver `docker-compose.yml`) corre en el mainframe junto
a la radio y, en cada ciclo:

1. Renderiza un batch fresco de tracks (preset `station`, seeds que avanzan).
2. **Reemplaza toda la biblioteca** en `./music` (borra tracks viejos, publica
   los nuevos de forma atómica).
3. Reinicia `radio-liquidsoap` para que la playlist en memoria olvide los
   borrados. Liquidsoap hace `reload_mode="watch"` para altas, pero conserva
   las bajas en memoria (`radio.liq`): sin reinicio, los temas borrados
   siguen sonando como fantasmas.

## Disparo

- **Continuo**: `INDIEGEN_INTERVAL_SECONDS` (default 6 h). Al arrancar NO corre
  un ciclo (`--no-run-on-start` en el compose): el primer ciclo llega por el
  intervalo o por trigger. A propósito — un ciclo en frío reemplazaría
  `./music` entero en el `up -d` (y hay un batch sin trackear en el box).
- **Push a indiegenerator**: el hook `scripts/post-receive` del repo
  `indiegenerator` (instalado en el remoto del mainframe) reconstruye el
  servicio y escribe `data/indiegen/trigger`, que dispara un ciclo inmediato.

## Requisitos en el mainframe

- `~/servers/radio` (este repo) y `~/servers/indiegenerator` (contexto de
  build, `INDIEGEN_CONTEXT`) como repos hermanos, o ajustar `INDIEGEN_CONTEXT`.
- Restart en modo **signal** (default): el generador escribe
  `data/indiegen/restart-request` al rotar, y un watcher del host hace el
  `docker compose restart liquidsoap`. Instalarlo:

```bash
# watcher: corre cada minuto, como kbdejavu (grupo docker). Sin sudo.
crontab -l | { cat; echo "* * * * * /home/kbdejavu/servers/radio/scripts/restart-on-signal.sh"; } | crontab -
```

No se monta `/var/run/docker.sock` en ningún contenedor: el socket en el
generador daba control docker root-equivalente sobre todo el host (que además
corre jellyfin/searxng/archive). El reinicio de liquidsoap es decisión de la
radio, ejecutada por el host vía cron.

## Operación

```bash
# ciclo inmediato (desde el host del mainframe)
./scripts/indiegen-cycle.sh

# logs
docker compose logs -f indiegen

# desactivar la generación automática sin tocar la radio
docker compose stop indiegen
```

### `.tmp` y el tmpfs de liquidsoap

El generador publica con rename atómico: escribe cada archivo en
`./music/.tmp` y lo renombra al nombre final. Ese staging vive dentro del
árbol que liquidsoap escanea, y `playlist("/music")` recorre subdirectorios —
así que liquidsoap intentaba decodificar archivos a medio escribir (y los
`.mix` viejos que quedaran ahí), en loop, un error por intento.

El servicio `liquidsoap` monta un `tmpfs` vacío sobre `/music/.tmp`: el
generador sigue viendo su carpeta real (el publish no cambia), liquidsoap ve
un directorio vacío. **No lo saques** creyendo que es un sobrante. Probado con
un canary en el host: 0 entradas adentro del contenedor. (Antes de elegir esto
se descartaron: `playlist` no tiene `recursive`; `request.directory` no existe
en 2.4.6; y `check_next` no sirve porque `request.filename` devuelve `""`
hasta que el request está resuelto.)

**NO correr `docker compose up -d --build indiegen` a secas.** `--build`
reconstruye también la imagen de icecast (su servicio tiene `build: ./icecast`)
y recrea el contenedor → corta el stream ~40 s (medido: EPIPE + reconexión de
liquidsoap a los 4 s, todo el montaje de oyentes desconectado). Para
rebuildear SÓLO el generador:

```bash
docker compose build indiegen
docker compose up -d --no-deps indiegen
```

`--no-deps` es la parte que importa: nombra SÓLO `indiegen`, así que compose no
toca la cadena `indiegen → liquidsoap → icecast`. **No agregues
`--no-recreate`**: su contrato es "si el contenedor ya existe, no lo recreen",
o sea que con una imagen nueva dejaría corriendo la vieja en silencio
(`docker compose up --help`, v2.40.3). Recrear indiegen es gratis —no sirve
audio—; recrear icecast cuesta ~40 s de stream.

## Coordinación

Cambios que afectan el contrato música↔radio (formato/nombre, montaje de
`./music`, reinicio de liquidsoap, variables `INDIEGEN_*`) deben revisarse en
ambos repos: `juanmanueldaza/indiegenerator` y `juanmanueldaza/radio`.
