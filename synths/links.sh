#!/bin/sh
# Keeps m00Dr wired to its synths in the PipeWire graph, re-creating any link
# that goes missing (m00Dr restarted, a synth restarted) every INTERVAL
# seconds. Ports are matched by substring because PipeWire's Midi-Bridge names
# embed ALSA client names. Each line of LINKS: "<output port>|<input port>".
#
# Replaces ~/code/mOODr/m8_midi_route.sh (m00Dr -> M8) now that m00Dr drives
# FluidSynth (ch1, ch3), the reese synth (ch2) and Open303/JC303 (ch5) instead.

INTERVAL=${INTERVAL:-2}
LINKS='RtMidiOut Client:m00Dr (capture)|Synth input port (m00Dr-FluidSynth:0) (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-Reese:bass in (playback)'

# Prints the first port in `pw-link -o` / `-i` (per $1) containing $2.
find_port() {
    pw-link "$1" | grep -F -m1 -- "$2"
}

while :; do
    echo "$LINKS" | while IFS='|' read -r out in; do
        [ -z "$out" ] && continue
        src=$(find_port -o "$out") dst=$(find_port -i "$in")
        [ -n "$src" ] && [ -n "$dst" ] || continue
        # pw-link fails with "File exists" when already linked: the steady state.
        pw-link "$src" "$dst" 2>/dev/null && echo "Linked $src -> $dst"
    done
    sleep "$INTERVAL"
done
