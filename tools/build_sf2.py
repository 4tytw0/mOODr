# /// script
# dependencies = ["numpy", "soundfile"]
# ///
# Builds a one-shot stab SoundFont: one preset per sample, each mapped across
# the keyboard at its detected root. Usage: build_sf2.py OUT.sf2 IN.wav[=CENTS]...
# A trailing =CENTS nudges that sample's tuning after detection. A trailing @loop
# (before any =CENTS) plays the whole file as a sustained loop instead of a one-shot.
import struct, sys, numpy as np, soundfile as sf

def f0(x, sr):
    x = x - x.mean(); n = len(x)
    c = np.fft.irfft(np.abs(np.fft.rfft(x, 2*n))**2)[:n]
    lo, hi = int(sr/2000), int(sr/40)
    k = lo + np.argmax(c[lo:hi]); conf = c[k]/c[0]
    a, b, d = c[k-1], c[k], c[k+1]; k = k + 0.5*(a-d)/(a-2*b+d)
    return sr/k, conf

def load(p, loop=False):
    x, sr = sf.read(p, always_2d=True); m = x.mean(1)
    if not loop: on = np.argmax(np.abs(m) > 0.01*np.abs(m).max()); m = m[on:]
    m = m/np.abs(m).max()*0.9
    if not loop: f = int(0.01*sr); m[-f:] *= np.linspace(1, 0, f)  # declick the tail
    pk = int(np.argmax(np.abs(m)))
    fr = [f0(m[i:i+4096], sr) for i in range(pk, max(pk+1, len(m)-4096), 1024)]
    fs = [f for f, c in fr if c > 0.5] or [max(fr, key=lambda t: t[1])[0]]
    midi = 69+12*np.log2(np.median(fs)/440); root = round(midi)
    return (m*32767).astype('<i2'), sr, root, round(100*(root-midi))

def ck(tag, data): return tag + struct.pack('<I', len(data)) + data + (b'\0' if len(data) % 2 else b'')
def lst(tag, *chunks): return ck(b'LIST', tag + b''.join(chunks))
def name(s): return s.encode()[:19].ljust(20, b'\0')

out, paths = sys.argv[1], sys.argv[2:]
smpl = b''; shdr = b''; inst = b''; ibag = b''; igen = b''; phdr = b''; pbag = b''; pgen = b''
for i, p in enumerate(paths):
    p, _, nudge = p.partition('='); p, _, flag = p.partition('@'); loop = flag == 'loop'
    n = p.rsplit('/', 1)[1].rsplit('.', 1)[0]
    pcm, sr, root, corr = load(p, loop); corr += int(nudge or 0)
    start = len(smpl)//2; lend = start + len(pcm)
    if loop: pcm = np.concatenate([pcm, pcm[:16]])  # guard points past the loop end for interpolation
    smpl += pcm.tobytes() + b'\0'*92; end = start + len(pcm)
    shdr += name(n) + struct.pack('<IIIIIBbHH', start, end, start, lend, sr, root, corr, 0, 1)
    inst += name(n) + struct.pack('<H', i); ibag += struct.pack('<HH', len(igen)//4, 0)
    igen += struct.pack('<HBB', 43, 0, 127)
    if loop:  # 10 ms attack, 0.3 s release, -22 dB nominal (FluidSynth applies 0.4x, so ~9 dB) so held chords don't clip, loop on
        igen += struct.pack('<Hh', 34, -7973) + struct.pack('<Hh', 38, -2084) + struct.pack('<HH', 48, 220) + struct.pack('<HH', 54, 1)
    else:
        igen += struct.pack('<Hh', 38, -2000) + struct.pack('<HH', 54, 0)
    igen += struct.pack('<HH', 53, i)
    phdr += name(n) + struct.pack('<HHHIII', i, 0, i, 0, 0, 0); pbag += struct.pack('<HH', i, 0)
    pgen += struct.pack('<HH', 41, i)
    print(f"preset {i:2}  {n:12} root {root} ({'C C# D D# E F F# G G# A A# B'.split()[root%12]}{root//12-1}) corr {corr:+d}c")
k = len(paths)
inst += name('EOI') + struct.pack('<H', k); ibag += struct.pack('<HH', len(igen)//4, 0); igen += b'\0'*4
phdr += name('EOP') + struct.pack('<HHHIII', 0, 0, k, 0, 0, 0); pbag += struct.pack('<HH', k, 0); pgen += b'\0'*4
shdr += name('EOS') + b'\0'*26
body = b'sfbk' + lst(b'INFO', ck(b'ifil', struct.pack('<HH', 2, 1)), ck(b'isng', b'EMU8000\0'),
                     ck(b'INAM', b'gamebreaks stabs\0\0')) \
     + lst(b'sdta', ck(b'smpl', smpl)) \
     + lst(b'pdta', ck(b'phdr', phdr), ck(b'pbag', pbag), ck(b'pmod', b'\0'*10), ck(b'pgen', pgen),
           ck(b'inst', inst), ck(b'ibag', ibag), ck(b'imod', b'\0'*10), ck(b'igen', igen), ck(b'shdr', shdr))
open(out, 'wb').write(ck(b'RIFF', body))
