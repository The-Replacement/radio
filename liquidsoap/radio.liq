# radio.liq — minimal test
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

# Test 1: exact doc example pattern.
def host_transition(ending, starting) =
  sequence([ending.source, starting.source])
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
