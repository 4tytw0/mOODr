# /// script
# dependencies = ["numpy", "soundfile"]
# ///
# Renders one seamless loop of a distorted, phased sine at ~C4.
# Usage: phase_sine.py OUT.wav
import sys, numpy as np, soundfile as sf
SR, PERIOD, CYCLES = 44100, 168, 420          # 262.5 Hz; loop = 70560 samples = 1.6 s = one sweep
L = PERIOD * CYCLES; n = np.arange(L * 3)     # 3 loops; keep the last so the filter state has settled
x = np.sin(2 * np.pi * n / PERIOD)
x = np.tanh(3.0 * x) / np.tanh(3.0)           # drive: soft clip adds odd harmonics
lfo = 0.5 - 0.5 * np.cos(2 * np.pi * n / L)   # one sweep per loop
fc = 300 * (3000 / 300) ** lfo                # 300 Hz .. 3 kHz, exponential
t = np.tan(np.pi * fc / SR); a = (t - 1) / (t + 1)
y = np.empty_like(x); z = np.zeros(4); fb = 0.0
for i in range(len(x)):                       # 4 first-order allpasses with feedback
    s = x[i] + 0.6 * fb
    for k in range(4):
        o = a[i] * s + z[k]; z[k] = s - a[i] * o; s = o
    fb = s; y[i] = 0.5 * (x[i] + s)
y = y[-L:]; y = np.tanh(1.5 * y / np.abs(y).max())   # a touch more grit after the phaser
sf.write(sys.argv[1], (0.9 * y / np.abs(y).max()).astype(np.float32), SR, subtype='PCM_16')
print(f"wrote {L} samples, seam jump {abs(y[0] - y[-1]):.4f}")
