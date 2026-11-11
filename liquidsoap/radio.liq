# radio.liq — test doc pattern with log
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
  log.critical("Mario transition fired")
  sequence([(ending.source : source), (starting.source : source)])
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
