# radio.liq — la fuente de la radio privada.
#
# Toma todos los archivos de audio de /music, los reproduce en orden
# aleatorio y los empuja a Icecast en MP3 128 kbps.
#
# Es intencionalmente corto: cada línea de acá es el corazón del proyecto
# y querés poder leerlo entero.

# El contenedor corre como root; liquidsoap lo pide explícitamente.
settings.init.allow_root := true

# 3 = info. Subí a 4 si algo no arranca.
settings.log.level := 3

# ---------------------------------------------------------------- secretos

# OJO con la firma: getenv(default, nombre_variable).
# El nombre de la variable va SEGUNDO. Esto no es un typo.
password = getenv("", "ICECAST_SOURCE_PASSWORD")

if password == "" then
  log.critical("ICECAST_SOURCE_PASSWORD está vacío: Icecast va a rechazar la conexión.")
end

# ---------------------------------------------------------------- música

# Playlist sobre un directorio. reload_mode="watch" hace que los temas
# nuevos que agregues a ./music entren solos, sin reiniciar nada.
musica = playlist(
  mode    = "randomize",
  reload_mode = "watch",
  "/music"
)

# mksafe evita que el stream se caiga si la carpeta está vacía:
# en vez de morir, emite silencio. Una radio privada que se cae sola
# es peor que una radio en silencio.
radio = mksafe(musica)

# ---------------------------------------------------------------- salida

# Encoder: MP3 128 kbps. Es lo que reproduce CUALQUIER cosa
# (navegador, iOS, VLC, Android) sin discusiones de códecs.
enc = %mp3(bitrate=128, samplerate=44100, stereo=true)

output.icecast(
  enc,
  host        = "icecast",
  port        = 8000,
  user        = "source",
  password    = password,
  mount       = "/stream",
  name        = "Radio Privada",
  description = "Acceso con token",
  genre       = "varios",
  radio
)
