# FX samples

Foley and atmosphere WAVs for fx.py (48 kHz, mono or stereo). List each in
`../fx.json` with the MIDI note (84 and up, on channel 10) that plays it:

    {"samples": [{"note": 84, "file": "rain.wav", "loop": true},
                 {"note": 85, "file": "door-slam.wav"}]}

`loop: true` plays while the note is held; otherwise it's a one-shot.
