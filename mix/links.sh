#!/bin/sh
# Keeps m00Dr wired to its synths, the synths and the breaks to the m800Dr Mixer
# and the mixer to the m800dr_mix sink, re-creating any link that goes missing
# (something restarted) every INTERVAL seconds. Ports are matched by substring
# because PipeWire's Midi-Bridge names embed ALSA client names. Each line of
# LINKS: "<output port>|<input port>".
#
# Replaces ~/code/mOODr/m8_midi_route.sh (m00Dr -> M8) now that m00Dr drives
# FluidSynth (ch1 chords, ch3 arp, ch4 drums), the reese synth (ch2) and
# SunVox (sunvox303.py, ch5) instead.
# Since 2026-10-31 FluidSynth only reaches the arp strip (ch3): chords and
# drums are SunVox-only on the rig (ch1/ch4 still go out for hardware, PR #6). Open303 (ch5) was removed 2026-10-31.
# SunVox (sunvoxbass.py) replaced reese.py on ch2; reese.py is kept in
# synths/reference/ for its BPM-locked wobble (run it by hand, then swap the
# m00Dr-SunVoxBass lines for the same ones with m00Dr-Reese).

INTERVAL=${INTERVAL:-2}
MIXER=m800Dr-Mixer
LINKS="RtMidiOut Client:m00Dr (capture)|Synth input port (m00Dr-FluidSynth:0) (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-SunVoxBass:bass in (playback)
m00Drc-Roller:sound out (capture)|m00Dr-SunVoxBass:bass in (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-SunVox303:acid in (playback)
m00Drc-Roller:sound out (capture)|m00Dr-SunVox303:acid in (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-Breaks:breaks in (playback)
m00Drc-Roller:sound out (capture)|m00Dr-Breaks:breaks in (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-FX:fx in (playback)
m00Drc-Roller:sound out (capture)|m00Dr-FX:fx in (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-SunVoxDrums:drums in (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-SunVoxKeys:keys in (playback)
m00Dr-SunVoxKeys:output_FL|$MIXER:chords_L
m00Dr-SunVoxKeys:output_FR|$MIXER:chords_R
m00Dr-SunVoxBass:output_FL|$MIXER:bass_L
m00Dr-SunVoxBass:output_FR|$MIXER:bass_R
m00Dr-SunVoxDrums:output_FL|$MIXER:drums_L
m00Dr-SunVoxDrums:output_FR|$MIXER:drums_R
m00Dr-FluidSynth:l_02|$MIXER:arp_L
m00Dr-FluidSynth:r_02|$MIXER:arp_R
m00Dr-FluidSynth:fx_l_04|$MIXER:arp_L
m00Dr-FluidSynth:fx_r_04|$MIXER:arp_R
m00Dr-FluidSynth:fx_l_05|$MIXER:arp_L
m00Dr-FluidSynth:fx_r_05|$MIXER:arp_R
m00Dr-SunVox303:output_FL|$MIXER:acid_L
m00Dr-SunVox303:output_FR|$MIXER:acid_R
m00Dr-Breaks:output_FL|$MIXER:breaks_L
m00Dr-Breaks:output_FR|$MIXER:breaks_R
m00Dr-FX:output_FL|$MIXER:fx_L
m00Dr-FX:output_FR|$MIXER:fx_R
$MIXER:controls|RtMidiIn Client:m00Dr Control (playback)
$MIXER:controls|m00Drc-Roller:knobs in (playback)
$MIXER:out_L|m800dr_mix:playback_FL
$MIXER:out_R|m800dr_mix:playback_FR"

# Each pass reads the ports and links once and only runs pw-link for a link
# that is missing. Every pw-link call is a new PipeWire client, and making ~120
# of them every INTERVAL (two lookups and a link attempt per line) kept
# WirePlumber and PipeWire busy enough to make the Python mixer miss its
# deadlines: 20 dropouts in 30 s while running, none while paused.

# Prints the first port in $1 (a cached `pw-link -o` / `-i` listing) containing $2.
find_port() {
    printf '%s\n' "$1" | grep -F -m1 -- "$2"
}

# Links "src|dst" if it isn't in $EXISTING already.
ensure_link() {
    printf '%s\n' "$EXISTING" | grep -qxF -- "$1|$2" && return
    pw-link "$1" "$2" 2>/dev/null && echo "Linked $1 -> $2"
}

while :; do
    OUTS=$(pw-link -o) INS=$(pw-link -i)
    # `pw-link -l` lists each output followed by "  |-> input" lines.
    EXISTING=$(pw-link -l | awk '/^[^ ]/ { src = $0; next } /^ *\|-> / { sub(/^ *\|-> /, ""); print src "|" $0 }')
    echo "$LINKS" | while IFS='|' read -r out in; do
        [ -z "$out" ] && continue
        src=$(find_port "$OUTS" "$out") dst=$(find_port "$INS" "$in")
        [ -n "$src" ] && [ -n "$dst" ] || continue
        ensure_link "$src" "$dst"
    done
    # Hardware MIDI inputs (ALSA kernel clients: USB MIDI devices) drive the
    # mixer's faders, except Midi Through and the M8's own MIDI port.
    dst=$(find_port "$INS" "$MIXER:faders")
    [ -n "$dst" ] && aconnect -i | sed -n "s/^client [0-9]*: '\(.*\)' \[type=kernel.*/\1/p" |
        grep -v -e '^System$' -e '^Midi Through$' -e 'M8' |
        while read -r name; do
            printf '%s\n' "$OUTS" | grep -F "Midi-Bridge:$name:" | grep -F '(capture)' |
                while read -r src; do
                    ensure_link "$src" "$dst"
                done
        done
    sleep "$INTERVAL"
done
