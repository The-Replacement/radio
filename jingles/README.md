# Jingles (DJ)

Clips de audio cortos que el DJ reproduce ocasionalmente entre temas.
Nunca se colocan bajo `./music/` (Liquidsoap los escanearía dentro del playlist).

Formatos soportados: cualquiera que Liquidsoap pueda decodificar (mp3, wav, flac, ogg).
La selección es determinista: `jingles[seed % count]`.

Agregá tus propios jingles acá. El directorio se monta read-only en Liquidsoap
en `/jingles`.