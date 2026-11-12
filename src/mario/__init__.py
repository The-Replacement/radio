"""Mario — the host voice for the radio.

Deterministic TTS generator inspired by Mario Pergolini (in living tribute).
Reads a radio music library (catalog.json + sidecars) and renders short
spoken Host segments between tracks, cached by (current_seed, next_seed, time_bucket).
"""

__version__ = "0.1.0"
TEMPLATE_VERSION = "mario/2"
