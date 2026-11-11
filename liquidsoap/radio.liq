# radio.liq — the private radio's source.
#
# Takes every audio file in /music, plays them in random order and pushes them
# to Icecast in MP3 128 kbps. Between two tracks the DJ decides what happens:
# a Mario host segment, a station ident, an occasional jingle, or a plain
# crossfade. Everything is guarded so a missing file never drops the stream.
#
# It is intentionally short: every line here is the heart of the project
# and you want to be able to read it whole.

# The container runs as root; liquidsoap asks for it explicitly.
settings.init.allow_root := true

# 3 = info. Raise it to 4 if something does not start.
settings.log.level := 3

# ---------------------------------------------------------------- secrets

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

# ---------------------------------------------------------------- Mario (the host voice) + DJ (transitions/idents/jingles)
#
# Mario pre-renders short spoken segments into /host (mounted from
# ./data/host) and publishes the current time bucket in
# /host/current-bucket. Station idents (liners) are also served from /host,
# named liner_{ident_id}_{seed}_{bucket}.wav (alias: liner_{seed}_{bucket}.wav).
# Jingles live in /jingles (mounted from ./jingles), never under /music.
#
# Between two tracks the DJ builds a short spacer list and sequences it before
# the next track; if no spacer applies it crossfades. Idents are chosen by
# scanning /host (Mario stays the single source of truth for the pool). Policy:
#   - host segment always (when cached)
#   - station ident only when no host segment AND next_seed is even
#   - jingle only when next_seed mod 5 == 0 and < 2 spacers already
#   - at most 2 spacers total (bounded gap)
# Missing files / empty pools / bad seeds degrade to "no spacer" and the
# crossfade; outer mksafe prevents stream drops.

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
      s = file.contents("/host/current-bucket")
      string.trim(s)
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
  es0 = seed_from_comment(ec)
  ef = try ending.metadata["filename"] catch _ do "" end
  es = if es0 == "" then seed_from_filename(ef) else es0 end

  # Parse next seed.
  sc = try starting.metadata["comment"] catch _ do "" end
  ss0 = seed_from_comment(sc)
  sf = try starting.metadata["filename"] catch _ do "" end
  ss = if ss0 == "" then seed_from_filename(sf) else ss0 end

  bucket = host_bucket()

  # Degrade to the next track (no crossfade) when context is unknown.
  if es == "" or ss == "" or bucket == "" then
    (starting.source : source)
  else
    # Build the spacer list (capped at 2).
    spacers = ref([])

    # Host segment (always, when cached).
    host_path = "/host/#{es}_#{ss}_#{bucket}.wav"
    try
      if file.exists(host_path) then
        spacers := list.append(spacers(), [single(host_path)])
      end
    catch _ do
      ()
    end

    # Station ident: scan /host (Mario stays single source of truth).
    # Included when no host segment is available, or when a host segment
    # is present AND the incoming seed is even (seed-parity policy).
    if ss != "" and bucket != "" then
      try
        files = file.ls(pattern="liner_*_#{ss}_#{bucket}.wav", "/host")
        sorted_files = list.sort(fun(x,y) -> string.compare(x,y), files)
        if list.length(sorted_files) > 0 then
          ident_path = "/host/" ^ list.hd(sorted_files)
          host_present = list.length(spacers()) > 0
          include_ident = if host_present then
            try (int_of_string(ss) mod 2) == 0 catch _ do false end
          else
            true
          end
          if include_ident and list.length(spacers()) < 2 then
            try
              if file.exists(ident_path) then
                spacers := list.append(spacers(), [single(ident_path)])
              end
            catch _ do
              ()
            end
          end
        end
      catch _ do
        ()
      end
    end

    # Jingle (only when next_seed mod 5 == 0 and < 2 spacers).
    if ss != "" and list.length(spacers()) < 2 then
      try
        n = int_of_string(ss)
        if (n mod 5) == 0 then
          jfiles_raw = file.ls("/jingles")
          jfiles = list.sort(fun(x,y) -> string.compare(x,y), jfiles_raw)
          if list.length(jfiles) > 0 then
            idx = n mod list.length(jfiles)
            jingle_path = "/jingles/" ^ list.nth(default="", jfiles, idx)
            try
              if file.exists(jingle_path) then
                spacers := list.append(spacers(), [single(jingle_path)])
              end
            catch _ do
              ()
            end
          end
        end
      catch _ do
        ()
      end
    end

    # Play spacers then the next track, or crossfade if none.
    if list.length(spacers()) > 0 then
      sequence(list.append(spacers(), [(starting.source : source)]))
    else
      # Crossfade: 2s fade-in on the new track, 2s fade-out on the old.
      add([fade.in(duration=2.0, (starting.source : source)), fade.out(duration=2.0, (ending.source : source))])
    end
  end
end

radio = mksafe(cross(duration=3.0, host_transition, musica))

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
