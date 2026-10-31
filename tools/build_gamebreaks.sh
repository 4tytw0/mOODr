#!/bin/sh
# Rebuilds synths/gamebreaks.sf2: the gamestabs one-shots (presets 0-9) plus the
# looped phase_sine synth (preset 10). Preset order must match the `select`
# comments in synths/fluidsynth.cfg. Usage: tools/build_gamebreaks.sh [GAMESTABS_DIR]
set -e
cd "$(dirname "$0")/.."
D=${1:-$HOME/Documents/PRODUCTION/SAMPLES/GAMEBREAKS/gamestabs}
uv run -q tools/phase_sine.py synths/phase_sine.wav
# toa_lif=-18: its detected root plays 18 cents sharp, measured on a render.
uv run -q tools/build_sf2.py synths/gamebreaks.sf2 \
    "$D/smet_nor3.wav" "$D/toa_f.wav" "$D/toa_fm2.wav" "$D/toa_lif.wav=-18" "$D/toa_ns.wav" \
    "$D/toa_oue.wav" "$D/tyr_sav.wav" "$D/tyr_sav2.wav" "$D/vk_cfh2.wav" "$D/vk_sos.wav" \
    synths/phase_sine.wav@loop
