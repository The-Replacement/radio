# radio.liq — the private radio's source.
settings.init.allow_root := true
settings.log.level := 3

password = getenv("", "ICECAST_SOURCE_PASSWORD")

if password == "" then
  log.critical("ICECAST_SOURCE_PASSWORD is empty.")
end

musica = playlist(
  mode        = "randomize",
  reload_mode = "watch",
  "/music"
)

# Test: minimal cross transition inserting a fixed host segment.
def host_transition(ending, starting) =
  path = "/host/1094_1095_2026-11-11T00.wav"
  if file.exists(path) then
    sequence([single(path), starting.source])
  else
    starting.source
  end
end

radio = mksafe(cross(host_transition, musica))

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
