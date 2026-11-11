# radio.liq — the private radio's source.
#
# Takes every audio file in /music, plays them in random order and pushes them
# to Icecast in MP3 128 kbps. Between two tracks, if Mario (the host voice)
# has pre-rendered a segment for that (current_seed, next_seed, time_bucket)
# pair, it plays it first.
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
  mode        = "randomize",
  reload_mode = "watch",
  "/music"
)

# ---------------------------------------------------------------- Mario (the host voice)
#
# Mario pre-renders short spoken segments into /host (mounted from
# ./data/host) and publishes the current time bucket in
# /host/current-bucket. Between two tracks we play the cached segment for
# (current_seed, next_seed, bucket) if it exists; otherwise we go straight
# to the next track. Everything is guarded so a missing segment, a missing
# bucket or an unparseable seed degrades to "play the next track" and never
# drops the stream.

# Parse a seed from a COMMENT tag ("seed=1000; preset=station; v1").
def seed_from_comment(c) =
  if c == "" then
    ""
  else
    try
      m = r/seed=(?<seed>[0-9]+)/.exec(c)
      list.assoc(default="", "seed", m.groups)
    catch _ do
      ""
    end
  end
end

# Fallback: parse the seed from a filename ("..._s1000.flac").
def seed_from_filename(f) =
  if f == "" then
    ""
  else
    try
      m = r/_s(?<seed>[0-9]+)/.exec(f)
      list.assoc(default="", "seed", m.groups)
    catch _ do
      ""
    end
  end
end

# Read the current bucket Mario published. Empty string on any failure.
def host_bucket() =
  try
    if file.exists("/host/current-bucket") then
      file.contents("/host/current-bucket")
    else
      ""
    end
  catch _ do
    ""
  end
end

def host_transition(ending, starting) =
  # Parse current seed.
  ec = try ending.metadata["comment"] catch _ do "" end
  es = seed_from_comment(ec)
  if es == "" then
    ef = try ending.metadata["filename"] catch _ do "" end
    es = seed_from_filename(ef)
  end

  # Parse next seed.
  sc = try starting.metadata["comment"] catch _ do "" end
  ss = seed_from_comment(sc)
  if ss == "" then
    sf = try starting.metadata["filename"] catch _ do "" end
    ss = seed_from_filename(sf)
  end

  bucket = host_bucket()

  if es == "" or ss == "" or bucket == "" then
    log.critical("Mario: skip (es=#{es} ss=#{ss} bucket=#{bucket})")
    (starting.source : source)
  else
    path = "/host/#{es}_#{ss}_#{bucket}.wav"
    if file.exists(path) then
      log.critical("Mario: play #{path}")
      sequence([(single(path) : source(audio=pcm(stereo))), (starting.source : source)])
    else
      log.critical("Mario: miss #{path}")
      (starting.source : source)
    end
  end
end

# Log track changes to confirm Liquidsoap sees track boundaries.
musica.on_track(fun (m) -> log.critical("ONTRACK: " ^ list.assoc(default="?", "comment", m)))

radio = mksafe(cross(host_transition, musica))

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
