"""Verificacion exacta del modulo de teoria: parseo, transposicion, digitaciones."""
import sys
sys.path.insert(0, "src")
from chordlocal.theory import Chord, fingering, diagram, STD_TUNING, key_uses_flats

fails = []
def check(desc, got, want):
    if got != want:
        fails.append(f"{desc}: obtuvo {got!r}, esperaba {want!r}")

# --- parseo ---
check("Em7 root", Chord.parse("Em7").root, 4)
check("Em7 quality", Chord.parse("Em7").quality, "min7")
check("F#m7b5 root", Chord.parse("F#m7b5").root, 6)
check("F#m7b5 quality", Chord.parse("F#m7b5").quality, "min7b5")
check("Bbmaj7 root", Chord.parse("Bbmaj7").root, 10)
check("Bbmaj7 quality", Chord.parse("Bbmaj7").quality, "maj7")
check("C/G bass", Chord.parse("C/G").bass, 7)
check("Dsus4", Chord.parse("Dsus4").quality, "sus4")
check("Am", Chord.parse("Am").quality, "min")
check("G7", Chord.parse("G7").quality, "7")
check("silencio N", Chord.parse("N"), None)

# --- ida y vuelta de nombres ---
for label in ["C", "Am", "G7", "Em7", "Fmaj7", "Bm7b5", "Dsus4", "C#m", "Ab"]:
    c = Chord.parse(label)
    flats = "b" in label
    check(f"roundtrip {label}", c.name(flats), label)

# --- transposicion ---
check("C +2 = D", Chord.parse("C").transpose(2).name(), "D")
check("Am +3 = Cm", Chord.parse("Am").transpose(3).name(), "Cm")
check("B +1 = C", Chord.parse("B").transpose(1).name(), "C")
check("C -1 = B", Chord.parse("C").transpose(-1).name(), "B")
check("G/B +5 = C/E", Chord.parse("G/B").transpose(5).name(), "C/E")
# transponer 12 semitonos debe ser identidad
for label in ["C", "Am", "F#m7", "Bb7"]:
    check(f"+12 identidad {label}", Chord.parse(label).transpose(12).name(),
          Chord.parse(label).name())

# --- las notas del acorde son correctas ---
check("C notas", sorted(Chord.parse("C").pitches()), [0, 4, 7])
check("Am notas", sorted(Chord.parse("Am").pitches()), [0, 4, 9])
check("G7 notas", sorted(Chord.parse("G7").pitches()), [2, 5, 7, 11])

# --- digitaciones: el acorde que suena debe coincidir con las notas digitadas ---
def sounding(f):
    """Clases de altura que realmente suenan en una digitacion."""
    return {(STD_TUNING[i] + fr) % 12 for i, fr in enumerate(f.frets) if fr >= 0}

# Incluye deliberadamente los sus de las 12 fundamentales: eran el hueco que
# dejaba acordes "sin digitacion" en canciones reales.
SUS = [n + q for q in ("sus2", "sus4")
       for n in ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")]
for label in ["C", "D", "E", "Em", "G", "A", "Am", "F", "Bm", "G7", "Am7", "Dm",
              "C#", "Eb", "F#m", "Bb", "Abm", "D#m7", "Bbmaj7", "C#7",
              "Cmaj7", "Bm7b5", "Adim", "Em6", "F#m7", "B7", "Dmaj7"] + SUS:
    ch = Chord.parse(label)
    f = fingering(ch)
    if f is None:
        fails.append(f"digitacion {label}: no se encontro forma")
        continue
    want = set(ch.pitches())
    got = sounding(f)
    # Toda nota digitada debe pertenecer al acorde, y la fundamental debe estar.
    if not got <= want:
        fails.append(f"digitacion {label}: suena {sorted(got)} pero el acorde es {sorted(want)}")
    if ch.root not in got:
        fails.append(f"digitacion {label}: falta la fundamental")
    if f.max_fret() > 15:
        fails.append(f"digitacion {label}: traste {f.max_fret()} es impracticable")

# --- capo: la forma digitada debe sonar como el acorde pedido ---
for label, capo in [("G", 3), ("C", 2), ("D", 5), ("A", 4), ("E", 7)]:
    ch = Chord.parse(label)
    f = fingering(ch, capo=capo)
    if f is None:
        fails.append(f"capo {label}@{capo}: sin forma")
        continue
    # Con cejilla en `capo`, cada cuerda suena `capo` semitonos mas arriba.
    got = {(STD_TUNING[i] + fr + capo) % 12 for i, fr in enumerate(f.frets) if fr >= 0}
    want = set(ch.pitches())
    if not got <= want:
        fails.append(f"capo {label}@{capo}: suena {sorted(got)}, esperaba {sorted(want)}")

# --- armaduras con bemoles ---
check("F usa bemoles", key_uses_flats("F", "major"), True)
check("G no usa bemoles", key_uses_flats("G", "major"), False)
check("Dm usa bemoles", key_uses_flats("D", "minor"), True)

print(f"\nverificaciones fallidas: {len(fails)}")
for f in fails:
    print("  FALLO:", f)
print("OK" if not fails else "HAY FALLOS")
sys.exit(1 if fails else 0)
