# radio.liq — Mario host transition (infallible single, typed)
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
  ec = try ending.metadata["comment"] catch _ do "" end
  es = seed_from_comment(ec)
  if es == "" then
    ef = try ending.metadata["filename"] catch _ do "" end
    es = seed_from_filename(ef)
  end

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

# Log every track change to confirm transitions fire.
musica.on_track(fun (m) -> log.critical("Track changed: " ^ list.assoc(default="", "title", m)))

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
