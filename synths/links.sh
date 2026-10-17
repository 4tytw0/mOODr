#!/bin/sh
# Keeps m00Dr wired to its synths, the synths and the M8 to the m800Dr Mixer
# and the mixer to the m800dr_mix sink, re-creating any link that goes missing
# (something restarted) every INTERVAL seconds. Ports are matched by substring
# because PipeWire's Midi-Bridge names embed ALSA client names. Each line of
# LINKS: "<output port>|<input port>".
#
# Replaces ~/code/mOODr/m8_midi_route.sh (m00Dr -> M8) now that m00Dr drives
# FluidSynth (ch1 chords, ch3 arp, ch4 drums), the reese synth (ch2) and
# SunVox (sunvox303.py, ch5) instead. Open303/JC303 played ch5 before SunVox;
# to switch back, swap the m00Dr-SunVox303 lines for
#   RtMidiOut Client:m00Dr (capture)|m00Dr-Open303:events-in
#   m00Dr-Open303:audio-out1|$MIXER:acid_L
#   m00Dr-Open303:audio-out2|$MIXER:acid_R

INTERVAL=${INTERVAL:-2}
MIXER=m800Dr-Mixer
LINKS="RtMidiOut Client:m00Dr (capture)|Synth input port (m00Dr-FluidSynth:0) (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-Reese:bass in (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-SunVox303:acid in (playback)
m00Drc-Roller:sound out (capture)|m00Dr-SunVox303:acid in (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-SunVoxDrums:drums in (playback)
RtMidiOut Client:m00Dr (capture)|m00Dr-SunVoxKeys:keys in (playback)
m00Dr-FluidSynth:l_00|$MIXER:chords_L
m00Dr-FluidSynth:r_00|$MIXER:chords_R
m00Dr-FluidSynth:fx_l_00|$MIXER:chords_L
m00Dr-FluidSynth:fx_r_00|$MIXER:chords_R
m00Dr-FluidSynth:fx_l_01|$MIXER:chords_L
m00Dr-FluidSynth:fx_r_01|$MIXER:chords_R
m00Dr-SunVoxKeys:output_FL|$MIXER:chords_L
m00Dr-SunVoxKeys:output_FR|$MIXER:chords_R
m00Dr-Reese:output_FL|$MIXER:bass_L
m00Dr-Reese:output_FR|$MIXER:bass_R
m00Dr-FluidSynth:l_03|$MIXER:drums_L
m00Dr-FluidSynth:r_03|$MIXER:drums_R
m00Dr-FluidSynth:fx_l_06|$MIXER:drums_L
m00Dr-FluidSynth:fx_r_06|$MIXER:drums_R
m00Dr-FluidSynth:fx_l_07|$MIXER:drums_L
m00Dr-FluidSynth:fx_r_07|$MIXER:drums_R
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
m800dr_mix_m8_out:output_FL|$MIXER:m8_L
m800dr_mix_m8_out:output_FR|$MIXER:m8_R
$MIXER:controls|RtMidiIn Client:m00Dr Control (playback)
$MIXER:controls|m00Drc-Roller:knobs in (playback)
$MIXER:out_L|m800dr_mix:playback_FL
$MIXER:out_R|m800dr_mix:playback_FR"

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
    # Hardware MIDI inputs (ALSA kernel clients: USB MIDI devices) drive the
    # mixer's faders, except Midi Through and the M8's own MIDI port.
    dst=$(find_port -i "$MIXER:faders")
    [ -n "$dst" ] && aconnect -i | sed -n "s/^client [0-9]*: '\(.*\)' \[type=kernel.*/\1/p" |
        grep -v -e '^System$' -e '^Midi Through$' -e 'M8' |
        while read -r name; do
            pw-link -o | grep -F "Midi-Bridge:$name:" | grep -F '(capture)' |
                while read -r src; do
                    pw-link "$src" "$dst" 2>/dev/null && echo "Linked $src -> $dst"
                done
        done
    sleep "$INTERVAL"
done
