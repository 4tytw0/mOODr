// Generates an M8 .m8s project file pre-configured to match m00Dr's MIDI setup:
// chords (ch.1), arp (ch.2), bass (ch.3), acid (ch.4) -- see ../M8-SETUP.md.
//
// Built on m8-js (https://github.com/whitlockjc/m8-js), which is the only library found
// with confirmed read+write support for M8's binary file formats. Its Song/Instrument
// classes aren't part of the package's public API (only dumpM8File/loadM8File are), so
// this reaches into its internal modules directly, the same way the library's own CLI
// (lib/cli/import-json.js) does.
//
// IMPORTANT: this has NOT been verified against real M8 hardware. The file format version
// this writes (m8-js's LATEST_M8_VERSION) may or may not match what your M8 Headless
// firmware (reported as 3.1.4 over serial) actually expects -- M8's "file format version"
// and "firmware version" are different numbers that don't necessarily track together.
// Load the generated file on the M8 and confirm it opens cleanly before trusting it.

const { writeFileSync } = require('fs')
const path = require('path')

const { dumpM8File } = require('m8-js')
const Song = require('m8-js/lib/types/Song')
const Wavsynth = require('m8-js/lib/types/instruments/Wavsynth')

const song = new Song()

song.name = 'M00DR DEFAULT'

// --- Instruments -----------------------------------------------------------
// All four voices are WAVSYNTH (confirmed working/loadable on this hardware already --
// see M8-SETUP.md). Each is shaped to loosely match its m00Dr role rather than left as an
// undifferentiated default patch.

const chords = new Wavsynth()
chords.name = 'CHORDS'
chords.instrParams.shape = 0x05 // TRIANGLE -- soft, pad-like
chords.filterParams.type = 0x01 // LOWPASS
chords.filterParams.cutoff = 0xB0
chords.mixerParams.cho = 0x40 // width
chords.mixerParams.rev = 0x30 // a little space
chords.envelopes[0].dest = 0x01 // VOLUME
chords.envelopes[0].attack = 0x20 // slow-ish fade in, pad swell
chords.envelopes[0].decay = 0xA0

const arp = new Wavsynth()
arp.name = 'ARP'
arp.instrParams.shape = 0x02 // PULSE 50% -- bright, plucky
arp.filterParams.type = 0x01
arp.filterParams.cutoff = 0xE0
arp.filterParams.res = 0x20
arp.mixerParams.del = 0x30 // a little delay for sparkle
arp.mixerParams.rev = 0x10
arp.envelopes[0].dest = 0x01
arp.envelopes[0].attack = 0x00
arp.envelopes[0].decay = 0x40 // short/staccato

const bass = new Wavsynth()
bass.name = 'BASS'
bass.instrParams.shape = 0x00 // PULSE 12% -- punchy low end
bass.filterParams.type = 0x01
bass.filterParams.cutoff = 0x60 // dark
bass.filterParams.res = 0x10
bass.envelopes[0].dest = 0x01
bass.envelopes[0].attack = 0x00
bass.envelopes[0].hold = 0x10
bass.envelopes[0].decay = 0x60

const acid = new Wavsynth()
acid.name = 'ACID'
acid.instrParams.shape = 0x04 // SAW -- classic acid waveform
acid.filterParams.type = 0x01
acid.filterParams.cutoff = 0x40 // starts closed
acid.filterParams.res = 0xD0 // high resonance -> squelch
acid.envelopes[0].dest = 0x07 // CUTOFF -- filter envelope for the acid sweep
acid.envelopes[0].amount = 0xC0
acid.envelopes[0].attack = 0x00
acid.envelopes[0].decay = 0x50
acid.envelopes[1].dest = 0x01 // VOLUME -- short punchy amp env
acid.envelopes[1].attack = 0x00
acid.envelopes[1].decay = 0x40

song.instruments[0] = chords
song.instruments[1] = arp
song.instruments[2] = bass
song.instruments[3] = acid
// Re-wire each instrument's table to the Song's matching table slot, same as Song's own
// constructor does for its default None instruments.
;[chords, arp, bass, acid].forEach((instr, i) => {
  instr.table = song.tables[i]
})

// --- MIDI settings -----------------------------------------------------------
// Matches the confirmed-working on-device configuration in ../M8-SETUP.md. The M8 is the
// transport MASTER here: it sends clock/Start/Stop (via m8_to_moodr_transport_bridge.py) to
// drive m00Dr, and does not follow anything incoming.

song.midiSettings.receiveSync = false
song.midiSettings.receiveTransport = 0x00 // OFF -- M8 doesn't follow an external clock
song.midiSettings.sendSync = true
song.midiSettings.sendTransport = 0x02 // SONG -- M8 is the clock/transport master
song.midiSettings.recordNoteChannel = 0x09
song.midiSettings.trackInputMode = 0x02 // POLY -- lets multiple tracks on ch.1 sound a real chord

// Channels match moodr/playback.py: chords=ch1, bass=ch2, arp=ch3, acid=ch5 (ch4 unused --
// bass moved down to ch2 and arp/acid each moved up a channel to make room, per the
// 2026-09-19 channel reassignment). Track 1 (chords) is joined by tracks 5-7 on the same
// channel so a 3-4 note chord from m00Dr actually sounds as a chord (each M8 track is
// otherwise monophonic; POLY mode round-robins simultaneous notes on a shared channel
// across the tracks assigned to it).
song.midiSettings.trackInputChannel = [1, 3, 2, 5, 1, 1, 1, 1]
song.midiSettings.trackInputInstrument = [0, 1, 2, 3, 0, 0, 0, 0]

// --- Minimal song content -----------------------------------------------------------
// A blank Song (the default from `new Song()`) has no chains/phrases/song-rows programmed
// at all, which means the M8's own transport has nothing to iterate through: pressing Play
// in SONG mode immediately hits "end of song" and stops after essentially one step, and
// LIVE mode has no chain to select in the first place. This gives it one silent, looping
// chain (an empty Phrase -- no notes, since the actual notes come from m00Dr over MIDI) so
// the transport has something to run continuously, in either SONG or LIVE mode.
//
// Chain 0, step 0 -> Phrase 0 (left at its default 16 empty steps). Song row 0, track 1 ->
// Chain 0. Confirmed on real hardware (2026-09-19): without this, SONG mode only sends one
// Start-then-Stop blip (m00Dr plays exactly one note); with it, the transport keeps running
// and m00Dr plays its full sequence continuously.
song.chains[0].steps[0].phrase = 0x00
song.steps[0].tracks[0] = 0x00

// --- Write -----------------------------------------------------------

const outPath = path.join(__dirname, 'M00DR-DEFAULT.m8s')
writeFileSync(outPath, Buffer.from(dumpM8File(song)))

console.log(`Wrote ${outPath}`)
