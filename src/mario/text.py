"""Build the DJ announcement text for a Host segment.

Deterministic: same inputs -> identical text. The text template version is a
constant (TEMPLATE_VERSION) so bumping it invalidates all cached segments.
"""

from __future__ import annotations

from . import TEMPLATE_VERSION
from .tracks import TrackMeta
from .vibe import vibe_for

# Openers: the DJ claims the act of steering/transitioning (outgoing lane context).
_OPENERS_PULSE: tuple[str, ...] = (
    "Te dejo un rato más con este pulso... y lo suelto.",
    "Llevo este motor hasta el final... y aflojo la mano del volante.",
    "Mantengo el ritmo firme... hasta que decido doblar.",
)
_OPENERS_DRIFT: tuple[str, ...] = (
    "Dejo que esa deriva se disuelva... lento, a propósito.",
    "Mantengo el espacio abierto... y que se caiga.",
    "Aflojo la niebla... y que respire.",
)
_OPENERS_HUSH: tuple[str, ...] = (
    "Guardo el silencio cerca... y lo dejo ir, suave.",
    "Dejo que ese quieto flote... antes de mover el aire.",
    "Arrullo el silencio... y lo libero, despacio.",
)

# Cues: claim the act of playing/dropping the incoming track (incoming vibe).
_CUES_PULSE: tuple[str, ...] = (
    "Te deslizo hacia una corriente firme... {lane}, {form}. Que el movimiento te lleve.",
    "Te tiro {lane}, {form}... firme, hipnótico, sin cortes.",
    "Clavo la frecuencia en {lane}, {form}... que el ritmo te arrastre.",
)
_CUES_DRIFT: tuple[str, ...] = (
    "Te llevo hacia {lane}, {form}... ancho, liviano, sin fin.",
    "Abro {lane}, {form}... que te lleve lejos de los bordes.",
    "Pongo {lane}, {form} en órbita... lento, cielo abierto.",
)
_CUES_HUSH: tuple[str, ...] = (
    "Te bajo a {lane}, {form}... silencio, y un largo respiro.",
    "Coloco {lane}, {form} en la habitación... liviano, casi nada.",
    "Bajo las luces para {lane}, {form}... que el quieto te sostenga.",
)

# Clairvoyant/time images — subtle, atmospheric.
_VISIONS: tuple[str, ...] = (
    "Vi este giro antes de que llegara.",
    "La próxima forma ya esperaba en la señal.",
    "Sentí esta frecuencia mucho antes del fade.",
    "La sala me dijo a dónde ir... yo sólo escuché primero.",
    "Sabía que el aire necesitaba esto, justo ahora.",
)
_TIME_IMAGES: tuple[str, ...] = (
    "Esta hora cuelga baja... sin apuro.",
    "La hora se estira, fina y plateada.",
    "El tiempo se mueve como agua acá... lento y seguro.",
    "El reloj zumba suave en la oscuridad.",
)


def _pick(options: tuple[str, ...], key: int) -> str:
    return options[key % len(options)]


def _openers_for(mood: str) -> tuple[str, ...]:
    if mood == "pulse":
        return _OPENERS_PULSE
    if mood == "hush":
        return _OPENERS_HUSH
    return _OPENERS_DRIFT


def _cues_for(mood: str) -> tuple[str, ...]:
    if mood == "pulse":
        return _CUES_PULSE
    if mood == "hush":
        return _CUES_HUSH
    return _CUES_DRIFT


def _bucket_hour(bucket: str) -> int | None:
    if "T" not in bucket:
        return None
    hour = bucket.split("T", 1)[1][:2]
    try:
        return int(hour)
    except ValueError:
        return None


def build_text(
    current: TrackMeta, nxt: TrackMeta, bucket: str, template_version: str = TEMPLATE_VERSION
) -> str:
    """Return the DJ announcement text. Deterministic in (current, nxt, bucket, version)."""
    # template_version is part of the contract/identity (cache invalidation signal).
    _ = template_version
    vibe = vibe_for(current, nxt, bucket)
    mix = current.seed * 131542391 + nxt.seed * 2654435761 + sum(ord(c) for c in bucket)

    opener = _pick(_openers_for(vibe.current_mood), mix)
    cue = _pick(_cues_for(vibe.next_mood), mix >> 3).format(lane=nxt.lane, form=nxt.form)

    parts = [opener]
    # If the character shifts, name the crossing softly.
    if vibe.current_mood != vibe.next_mood:
        shifts = {
            ("pulse", "drift"): "Aflojo la sala... que los bordes se blur.",
            ("pulse", "hush"): "Bajo el movimiento... que todo se asiente.",
            ("drift", "pulse"): "Recojo la deriva... y le doy columna.",
            ("drift", "hush"): "Pliego el espacio hacia adentro... hacia el quieto.",
            ("hush", "pulse"): "Levanto el silencio... y le pongo un pulso debajo.",
            ("hush", "drift"): "Abro el silencio... y que vague.",
        }
        parts.append(shifts.get((vibe.current_mood, vibe.next_mood), "Giro la sala, suave."))
    parts.append(cue)

    # Subtle clairvoyant/time image, chosen deterministically.
    hour = _bucket_hour(bucket)
    if hour is not None and ((mix >> 5) % 3 == 0):
        # Weave time atmospherically; hour value keeps it tied to bucket but evocative.
        time_line = _pick(_TIME_IMAGES, mix >> 7)
        if hour < 6:
            time_line = "La noche cuelga baja... sin apuro."
        elif hour < 12:
            time_line = "La luz de la mañana, apenas... tomando su tiempo."
        elif hour < 18:
            time_line = "La tarde deriva... fácil, sin forzar."
        else:
            time_line = "La noche se asienta... lenta y segura."
        # Blend with a generic image occasionally for variation.
        if (mix >> 9) % 2 == 0:
            time_line = _pick(_TIME_IMAGES, mix >> 7)
        parts.append(time_line)
    else:
        parts.append(_pick(_VISIONS, mix >> 5))

    return " ".join(parts)