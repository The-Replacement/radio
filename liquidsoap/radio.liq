# radio.liq — enable telnet for debugging
settings.init.allow_root := true
settings.log.level := 3

# Enable telnet server for debugging (skip tracks to test transitions).
settings.server.telnet := true
settings.server.bind_addr := "0.0.0.0"
settings.server.port := 1234

password = getenv("", "ICECAST_SOURCE_PASSWORD")

if password == "" then
  log.critical("ICECAST_SOURCE_PASSWORD is empty.")
end

musica = playlist(
  mode        = "randomize",
  reload_mode = "watch",
  "/music"
)

def host_transition(ending, starting) =
  log.critical("Mario transition fired")
  sequence([(single(fallible=true, "/host/1094_1095_2026-11-11T00.wav") : source), (starting.source : source)])
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
  radio
)
