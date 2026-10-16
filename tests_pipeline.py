"""Prueba end-to-end con audio sintetico de progresion conocida.

Genera una grabacion con acordes de guitarra reales (usando las digitaciones del
modulo de teoria) mas percusion, corre el pipeline completo y mide que porcentaje
del tiempo el acorde detectado coincide con la verdad conocida.
"""
import sys
sys.path.insert(0, "src")
import numpy as np
import soundfile as sf
from pathlib import Path
from chordlocal.theory import Chord, fingering, STD_TUNING

SR = 44100
BPM = 90.0
BEAT = 60.0 / BPM
BARS_PER_CHORD = 1
BEATS_PER_BAR = 4
PROGRESSION = ["G", "Em", "C", "D"]
REPEATS = 4

def pluck(freq, dur, sr=SR, amp=0.25):
    """Nota con armonicos y decaimiento exponencial: se parece a una cuerda."""
    n = int(dur * sr)
    t = np.arange(n) / sr
    env = np.exp(-2.2 * t)
    sig = np.zeros(n)
    # Armonicos con amplitud decreciente, como una cuerda pulsada real.
    for h, a in enumerate([1.0, 0.5, 0.33, 0.22, 0.14, 0.09], start=1):
        sig += a * np.sin(2 * np.pi * freq * h * t + np.random.rand() * 0.1)
    return amp * sig * env / 2.2

def strum(chord_label, dur):
    """Rasgueo de un acorde usando su digitacion real en la guitarra."""
    ch = Chord.parse(chord_label)
    f = fingering(ch)
    pitches = [STD_TUNING[i] + fr for i, fr in enumerate(f.frets) if fr >= 0]
    n = int(dur * SR)
    out = np.zeros(n)
    for k, midi in enumerate(pitches):
        freq = 440.0 * 2 ** ((midi - 69) / 12.0)
        # Retardo progresivo entre cuerdas: lo que hace que sea un rasgueo.
        delay = int(k * 0.012 * SR)
        v = pluck(freq, dur - delay / SR)
        out[delay:delay + len(v)] += v[:max(0, n - delay)]
    return out

def click(dur, low=True):
    """Golpe percusivo para que el detector de beats tenga de donde agarrarse."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    if low:
        sig = np.sin(2 * np.pi * 60 * t) * np.exp(-28 * t) * 0.5
    else:
        sig = np.random.randn(n) * np.exp(-55 * t) * 0.16
    return sig

print("generando audio de prueba...")
total_beats = len(PROGRESSION) * REPEATS * BARS_PER_CHORD * BEATS_PER_BAR
total = int(total_beats * BEAT * SR) + SR
audio = np.zeros(total)
truth = []   # (inicio, fin, acorde)

beat_i = 0
for rep in range(REPEATS):
    for label in PROGRESSION:
        t_start = beat_i * BEAT
        bar_dur = BEATS_PER_BAR * BEAT * BARS_PER_CHORD
        truth.append((t_start, t_start + bar_dur, label))
        for b in range(BEATS_PER_BAR * BARS_PER_CHORD):
            pos = int((beat_i + b) * BEAT * SR)
            # Acorde en cada beat, sostenido hasta el siguiente.
            s = strum(label, BEAT * 1.05)
            audio[pos:pos + len(s)] += s[:max(0, total - pos)]
            c = click(0.14, low=(b % 2 == 0))
            audio[pos:pos + len(c)] += c[:max(0, total - pos)]
        beat_i += BEATS_PER_BAR * BARS_PER_CHORD

audio /= np.max(np.abs(audio)) * 1.05
test_wav = Path("cache/_test_progresion.wav")
test_wav.parent.mkdir(exist_ok=True)
sf.write(str(test_wav), audio, SR)
print(f"  {test_wav}  {len(audio)/SR:.1f}s  progresion: {' '.join(PROGRESSION)} x{REPEATS} @ {BPM} BPM")

# --- correr el pipeline ---
from chordlocal.fetch import Source
from chordlocal.analyze import analyze

src = Source(wav=test_wav, title="prueba progresion", origin=str(test_wav),
             duration=len(audio)/SR, video_id="_test")
print("\ncorriendo analisis...")
a = analyze(src, with_notes=False)

print(f"\n  BPM detectado    : {a.tempo['bpm']}  (real {BPM})")
print(f"  compas detectado : {a.tempo['meter']}  (real 4/4)")
print(f"  tonalidad        : {a.key['tonic']} {a.key['mode']}  (real G major)")
print(f"  cambios acorde   : {len([c for c in a.chords if c['root'] is not None])}")

# --- medir precision: muestreo cada 10 ms ---
def truth_at(t):
    for s, e, lab in truth:
        if s <= t < e:
            return lab
    return None

def pred_at(t):
    for c in a.chords:
        if c["start"] <= t < c["end"]:
            return c["label"]
    return None

grid = np.arange(0.0, truth[-1][1], 0.01)
hits = exact = compared = 0
confusion = {}
for t in grid:
    want, got = truth_at(t), pred_at(t)
    if want is None or got is None:
        continue
    compared += 1
    wc, gc = Chord.parse(want), Chord.parse(got)
    if gc and wc:
        if gc.root == wc.root and gc.quality == wc.quality:
            exact += 1; hits += 1
        elif gc.root == wc.root:
            hits += 1   # fundamental correcta, calidad distinta (ej. G vs Gmaj7)
        else:
            confusion[f"{want}->{got}"] = confusion.get(f"{want}->{got}", 0) + 1
    
print(f"\n  PRECISION (ponderada por tiempo, {compared} muestras)")
print(f"    acorde exacto (fundamental+calidad): {exact/compared:6.1%}")
print(f"    fundamental correcta               : {hits/compared:6.1%}")
if confusion:
    print("\n  confusiones principales:")
    for k, v in sorted(confusion.items(), key=lambda x: -x[1])[:6]:
        print(f"    {k:18s} {v*0.01:5.1f}s")

print("\n  secuencia detectada (primeros 12 tramos):")
for c in a.chords[:12]:
    print(f"    c.{c['bar']:<3} {c['start']:6.2f}-{c['end']:6.2f}  {c['label']:8s} conf={c['confidence']:.2f}")

ok = (exact / compared) > 0.70
print(f"\n{'OK' if ok else 'PRECISION BAJA'}: exactitud {exact/compared:.1%}")
sys.exit(0 if ok else 1)
