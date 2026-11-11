# radio.liq — test single() with literal path
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

def host_transition(ending, starting) =
  if file.exists("/host/1094_1095_2026-11-11T00.wav") then
    sequence([ending.source, single("/host/1094_1095_2026-11-11T00.wav"), starting.source])
  else
    sequence([ending.source, starting.source])
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
  radio
)
