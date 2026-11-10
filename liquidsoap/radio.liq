# radio.liq — the private radio's source.
#
# Takes every audio file in /music, plays them in random order and pushes them
# to Icecast in MP3 128 kbps.
#
# It is intentionally short: every line here is the heart of the project
# and you want to be able to read it whole.

# The container runs as root; liquidsoap asks for it explicitly.
settings.init.allow_root := true

# 3 = info. Raise it to 4 if something does not start.
settings.log.level := 3

# ---------------------------------------------------------------- secretos

# CAREFUL with the signature: getenv(default, variable_name).
# The variable name goes SECOND. This is not a typo.
password = getenv("", "ICECAST_SOURCE_PASSWORD")

if password == "" then
  log.critical("ICECAST_SOURCE_PASSWORD is empty: Icecast will reject the connection.")
end

# ---------------------------------------------------------------- music

# Playlist over a directory. reload_mode="watch" makes the new tracks you add
# to ./music come in on their own, with nothing to restart.
# CAREFUL: DELETIONS are not removed on their own (Liquidsoap keeps them in its
# in-memory random playlist). To remove tracks, restart liquidsoap:
#   docker compose restart liquidsoap
musica = playlist(
  mode    = "randomize",
  reload_mode = "watch",
  "/music"
)

# mksafe keeps the stream from falling if the folder is empty:
# instead of dying, it emits silence. A private radio that falls on its own
# is worse than a silent radio.
radio = mksafe(musica)

# ---------------------------------------------------------------- salida

# Encoder: MP3 128 kbps. It is what ANYTHING plays
# (browser, iOS, VLC, Android) without codec arguments.
enc = %mp3(bitrate=128, samplerate=44100, stereo=true)

output.icecast(
  enc,
  host        = "icecast",
  port        = 8000,
  user        = "source",
  password    = password,
  mount       = "/stream",
  name        = "Radio Privada",
  description = "Token access",
  genre       = "various",
  radio
)
