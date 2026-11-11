# Jingles (DJ)

Short audio clips played occasionally between tracks by the DJ transition.
Never placed under `./music/` (Liquidsoap would scan them into the playlist).

Supported formats: any format Liquidsoap can decode (mp3, wav, flac, ogg).
Selection is deterministic: `jingles[seed % count]`.

Add your own jingles here. The directory is mounted read-only into Liquidsoap
at `/jingles`.
