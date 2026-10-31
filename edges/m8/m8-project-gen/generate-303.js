// Generates an M8 .m8s project matching m00Dr's MIDI setup, with the four instruments
// voiced from what transcriptions of real TB-303 acid lines actually do -- see the
// 2026-09-20 ROADMAP entry and moodr/playback.py's _generate_acid_pattern.
//
// Channels follow moodr/playback.py, NOT the stale comment at the top of generate.js:
//
//     chords  CHORD_CHANNEL = 0  -> MIDI ch.1 -> M8 tracks 1, 5, 6, 7
//     bass    BASS_CHANNEL  = 1  -> MIDI ch.2 -> M8 track 3
//     arp     ARP_CHANNEL   = 2  -> MIDI ch.3 -> M8 track 2
//     acid    ACID_CHANNEL  = 4  -> MIDI ch.5 -> M8 track 4   (ch.4 deliberately unused)
//
// Built on m8-js, reaching into its internal type modules the same way the library's own
// CLI does, since only dumpM8File/loadM8File are public. See generate.js for the history.
//
// NOT VERIFIED ON HARDWARE. The voicing is a considered starting point, not a tested
// patch -- load it, and expect to turn the acid CUTOFF/RES/decay by ear.

const { writeFileSync } = require('fs')
const path = require('path')

const { dumpM8File } = require('m8-js')
const Song = require('m8-js/lib/types/Song')
const Wavsynth = require('m8-js/lib/types/instruments/Wavsynth')

// M8 track input modes (m8-js MIDISettings.trackInputModeToStr).
const MONO = 0x00
const LEGATO = 0x01
const POLY = 0x02

// Wavsynth oscillator shapes.
const PULSE12 = 0x00
const PULSE25 = 0x01
const PULSE50 = 0x02
const SAW = 0x04

const LOWPASS = 0x01

// Envelope/LFO destinations.
const DEST_VOLUME = 0x01
const DEST_CUTOFF = 0x07

function buildSong (trackInputMode, name) {
  const song = new Song()
  song.name = name

  // --- CHORDS (ch.1) -------------------------------------------------------
  // m00Dr can play this voice sustained or as house-style off-beat stabs, so the
  // attack has to be immediate -- a slow pad swell would smear every stab. Body
  // comes from chorus width instead of from a long attack.
  const chords = new Wavsynth()
  chords.name = 'CHORDS'
  chords.instrParams.shape = PULSE50 // hollow, organ-ish: the house chord sound
  chords.filterParams.type = LOWPASS
  chords.filterParams.cutoff = 0xA8
  chords.filterParams.res = 0x18
  chords.mixerParams.cho = 0x50
  chords.mixerParams.rev = 0x38
  chords.envelopes[0].dest = DEST_VOLUME
  chords.envelopes[0].attack = 0x00 // stabs need to land on the beat, not after it
  chords.envelopes[0].hold = 0x40
  chords.envelopes[0].decay = 0xB0

  // --- BASS (ch.2) ---------------------------------------------------------
  // Kept deliberately dark and simple. The acid voice is a resonant saw living in
  // the same low-mid range, so anything brighter here fights it; this holds the
  // sub and stays out of the way.
  const bass = new Wavsynth()
  bass.name = 'BASS'
  bass.instrParams.shape = PULSE12 // odd harmonics give it definition on small speakers
  bass.filterParams.type = LOWPASS
  bass.filterParams.cutoff = 0x55
  bass.filterParams.res = 0x10
  bass.envelopes[0].dest = DEST_VOLUME
  bass.envelopes[0].attack = 0x00
  bass.envelopes[0].hold = 0x18
  bass.envelopes[0].decay = 0x60

  // --- ARP (ch.3) ----------------------------------------------------------
  const arp = new Wavsynth()
  arp.name = 'ARP'
  arp.instrParams.shape = PULSE25 // thinner than the chords so it cuts above them
  arp.filterParams.type = LOWPASS
  arp.filterParams.cutoff = 0xE0
  arp.filterParams.res = 0x28
  arp.mixerParams.del = 0x38
  arp.mixerParams.rev = 0x18
  arp.envelopes[0].dest = DEST_VOLUME
  arp.envelopes[0].attack = 0x00
  arp.envelopes[0].decay = 0x38 // staccato

  // --- ACID (ch.5) ---------------------------------------------------------
  // The one voice the 303 research actually changes. A 303 is a sawtooth through a
  // steep resonant lowpass, with a fast envelope on the *cutoff* -- the squelch is
  // that filter sweep repeating on every note, not the oscillator.
  //
  // Three settings do the work, and all three are worth turning by ear on hardware:
  //   CUTOFF starts low, so the envelope has somewhere to sweep from. Opening this
  //          up is the single fastest way to make the line brighter.
  //   RES    high. The guides are unanimous that resonance wants to be at least
  //          two-thirds up before it reads as acid at all.
  //   DE1    the envelope decay is the 303's own "DECAY" knob: short gives each
  //          step a distinct blip, long smears the steps into one another.
  //
  // Dry and upfront on purpose: reverb on an acid line blurs exactly the 16th-note
  // articulation that m00Dr is generating.
  const acid = new Wavsynth()
  acid.name = 'ACID'
  acid.instrParams.shape = SAW
  acid.filterParams.type = LOWPASS
  acid.filterParams.cutoff = 0x30 // starts closed; the env opens it
  acid.filterParams.res = 0xE0 // squelch
  acid.ampParams.amp = 0x40 // a little drive -- a 303 is almost always pushed
  acid.ampParams.limit = 0x01
  acid.mixerParams.del = 0x20
  acid.mixerParams.rev = 0x00
  acid.envelopes[0].dest = DEST_CUTOFF
  acid.envelopes[0].amount = 0xD0
  acid.envelopes[0].attack = 0x00
  acid.envelopes[0].hold = 0x00
  acid.envelopes[0].decay = 0x40
  acid.envelopes[1].dest = DEST_VOLUME
  acid.envelopes[1].attack = 0x00
  acid.envelopes[1].hold = 0x20
  acid.envelopes[1].decay = 0x50

  song.instruments[0] = chords
  song.instruments[1] = arp
  song.instruments[2] = bass
  song.instruments[3] = acid
  // Re-wire each instrument's table to the Song's matching table slot, the same way
  // Song's own constructor does for its default None instruments.
  ;[chords, arp, bass, acid].forEach((instr, i) => {
    instr.table = song.tables[i]
  })

  // --- MIDI settings -------------------------------------------------------
  // The M8 is the transport master: it sends clock/Start/Stop (via
  // m8_to_moodr_transport_bridge.py) and follows nothing incoming.
  song.midiSettings.receiveSync = false
  song.midiSettings.receiveTransport = 0x00 // OFF
  song.midiSettings.sendSync = true
  song.midiSettings.sendTransport = 0x02 // SONG
  song.midiSettings.recordNoteChannel = 0x09
  // m00Dr's acid line sends accented steps at velocity 127 against 80 for plain ones
  // (ACID_ACCENT_VELOCITY / ACID_PLAIN_VELOCITY in playback.py). Velocity has to be
  // honoured for that difference to survive the trip.
  song.midiSettings.recordNoteVelocity = true
  song.midiSettings.trackInputMode = trackInputMode

  // Tracks 5-7 double track 1 on ch.1 so a 3-4 note chord from m00Dr sounds as a
  // chord: each M8 track is monophonic, and POLY round-robins simultaneous notes on a
  // shared channel across the tracks assigned to it.
  song.midiSettings.trackInputChannel = [1, 3, 2, 5, 1, 1, 1, 1]
  song.midiSettings.trackInputInstrument = [0, 1, 2, 3, 0, 0, 0, 0]

  // --- Minimal song content ------------------------------------------------
  // One silent looping chain so the M8's transport has something to iterate. Without
  // it, SONG mode hits "end of song" immediately and sends a single Start-then-Stop
  // blip -- confirmed on real hardware 2026-09-19. The notes come from m00Dr over
  // MIDI, so the phrase itself stays empty.
  song.chains[0].steps[0].phrase = 0x00
  song.steps[0].tracks[0] = 0x00

  return song
}

// Two builds, because M8's track input mode is a single global setting and the two
// voices want opposite things from it:
//
//   POLY   - chords sound as real chords (notes round-robin across tracks 1, 5, 6, 7),
//            but an acid slide retriggers the envelope instead of gliding into the
//            next note.
//   LEGATO - an overlapping note-on changes pitch without retriggering, which is
//            exactly what m00Dr's slide steps send and what makes a 303 slide sound
//            like a slide. The cost is that chords collapse to one note at a time.
//
// POLY is the default because m00Dr is a chord-progression tool first. Load the LEGATO
// build when the acid line is the point and the chords are muted or thinned.
const builds = [
  [POLY, 'M00DR 303', 'M00DR-303.m8s'],
  [LEGATO, 'M00DR 303 LG', 'M00DR-303-LEGATO.m8s']
]

for (const [mode, name, file] of builds) {
  const outPath = path.join(__dirname, file)
  writeFileSync(outPath, Buffer.from(dumpM8File(buildSong(mode, name))))
  console.log(`Wrote ${outPath}  (trackInputMode=${mode === POLY ? 'POLY' : 'LEGATO'})`)
}
