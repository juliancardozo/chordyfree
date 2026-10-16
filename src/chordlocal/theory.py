"""Teoria musical: parseo de acordes, transposicion, capo y digitaciones de guitarra.

Todo lo que Chordify cobra como "premium" (transponer, capo) vive aca y es
aritmetica modulo 12 sobre el resultado del analisis. No requiere re-analizar audio.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Nombres de notas. Usamos sostenidos por defecto y bemoles cuando la tonalidad
# lo pide, para que la partitura se lea natural (Bb major, no A# major).
SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

# Tonalidades que se escriben con bemoles.
FLAT_KEYS = {"F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb", "D-", "G-"}

PITCH = {
    "C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "Fb": 4,
    "E#": 5, "F": 5, "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9,
    "A#": 10, "Bb": 10, "B": 11, "Cb": 11, "B#": 0,
}

# Intervalos de cada calidad, en semitonos desde la fundamental.
QUALITIES: dict[str, tuple[int, ...]] = {
    "maj":    (0, 4, 7),
    "min":    (0, 3, 7),
    "dim":    (0, 3, 6),
    "aug":    (0, 4, 8),
    "sus2":   (0, 2, 7),
    "sus4":   (0, 5, 7),
    "7":      (0, 4, 7, 10),
    "maj7":   (0, 4, 7, 11),
    "min7":   (0, 3, 7, 10),
    "min7b5": (0, 3, 6, 10),
    "dim7":   (0, 3, 6, 9),
    "minmaj7":(0, 3, 7, 11),
    "6":      (0, 4, 7, 9),
    "min6":   (0, 3, 7, 9),
    "9":      (0, 4, 7, 10, 14),
    "maj9":   (0, 4, 7, 11, 14),
    "min9":   (0, 3, 7, 10, 14),
    "add9":   (0, 4, 7, 14),
    "5":      (0, 7),
}

# Sufijos tal como se imprimen. Orden de parseo: los mas largos primero, para
# que "min7b5" no se parsee como "min" + basura.
SUFFIX_TO_QUALITY: list[tuple[str, str]] = [
    ("maj7", "maj7"), ("maj9", "maj9"), ("min7b5", "min7b5"), ("m7b5", "min7b5"),
    ("minmaj7", "minmaj7"), ("mmaj7", "minmaj7"), ("dim7", "dim7"), ("min7", "min7"),
    ("min9", "min9"), ("min6", "min6"), ("add9", "add9"), ("sus2", "sus2"),
    ("sus4", "sus4"), ("dim", "dim"), ("aug", "aug"), ("m7", "min7"), ("m9", "min9"),
    ("m6", "min6"), ("maj", "maj"), ("min", "min"), ("M7", "maj7"), ("m", "min"),
    ("7", "7"), ("9", "9"), ("6", "6"), ("5", "5"), ("sus", "sus4"), ("", "maj"),
]

PRINT_SUFFIX = {
    "maj": "", "min": "m", "dim": "dim", "aug": "aug", "sus2": "sus2", "sus4": "sus4",
    "7": "7", "maj7": "maj7", "min7": "m7", "min7b5": "m7b5", "dim7": "dim7",
    "minmaj7": "mMaj7", "6": "6", "min6": "m6", "9": "9", "maj9": "maj9",
    "min9": "m9", "add9": "add9", "5": "5",
}


@dataclass
class Chord:
    """Un acorde como dato: fundamental 0-11 + calidad. Inmutable en la practica."""
    root: int
    quality: str = "maj"
    bass: int | None = None  # para inversiones: C/G

    @classmethod
    def parse(cls, label: str) -> "Chord | None":
        """Parsea 'Em7', 'C/G', 'F#m7b5'. Devuelve None para silencio ('N', 'X')."""
        s = (label or "").strip()
        if not s or s in ("N", "X", "NC", "N.C."):
            return None

        bass = None
        if "/" in s:
            s, _, bass_name = s.partition("/")
            bass = PITCH.get(bass_name.strip().capitalize())

        s = s.strip()
        # La fundamental son 1-2 caracteres: letra + alteracion opcional.
        if len(s) >= 2 and s[1] in ("#", "b"):
            root_name, rest = s[:2], s[2:]
        else:
            root_name, rest = s[:1], s[1:]

        root = PITCH.get(root_name.capitalize())
        if root is None:
            return None

        for suffix, quality in SUFFIX_TO_QUALITY:
            if rest == suffix:
                return cls(root=root, quality=quality, bass=bass)

        # Sufijo no reconocido: degradamos a triada en vez de descartar el acorde.
        return cls(root=root, quality="maj", bass=bass)

    def transpose(self, semitones: int) -> "Chord":
        """Transposicion. Esto es el feature premium #5 de Chordify, completo."""
        return Chord(
            root=(self.root + semitones) % 12,
            quality=self.quality,
            bass=None if self.bass is None else (self.bass + semitones) % 12,
        )

    def name(self, flats: bool = False) -> str:
        names = FLAT if flats else SHARP
        out = names[self.root] + PRINT_SUFFIX.get(self.quality, "")
        if self.bass is not None and self.bass != self.root:
            out += "/" + names[self.bass]
        return out

    def pitches(self) -> tuple[int, ...]:
        """Alturas absolutas (pitch classes sin octava) del acorde."""
        return tuple((self.root + i) % 12 for i in QUALITIES.get(self.quality, (0, 4, 7)))

    def __str__(self) -> str:
        return self.name()


def key_uses_flats(tonic: str, mode: str = "major") -> bool:
    """Decide si la cancion se escribe con bemoles, para que la partitura se lea bien."""
    t = (tonic or "C").strip()
    if mode.lower().startswith("min"):
        # La relativa mayor define la armadura: Am -> C, Cm -> Eb.
        idx = PITCH.get(t.capitalize())
        if idx is None:
            return False
        t = SHARP[(idx + 3) % 12]
    return t in FLAT_KEYS or "b" in t


# ----------------------------------------------------------------------------
# Digitaciones de guitarra (afinacion estandar)
# ----------------------------------------------------------------------------

# Cuerdas al aire, de la 6a (mas grave) a la 1a, en MIDI.
STD_TUNING = (40, 45, 50, 55, 59, 64)  # E2 A2 D3 G3 B3 E4

# Formas en posicion abierta. -1 = cuerda al aire no tocada (muteada).
# Orden: 6a cuerda primero. Estas son las que suenan mejor y las que se
# aprenden primero, asi que tienen prioridad sobre las cejillas moviles.
OPEN_SHAPES: dict[tuple[int, str], tuple[int, ...]] = {
    (0, "maj"):  (-1, 3, 2, 0, 1, 0),   # C
    (0, "7"):    (-1, 3, 2, 3, 1, 0),   # C7
    (0, "maj7"): (-1, 3, 2, 0, 0, 0),   # Cmaj7
    (0, "sus2"): (-1, 3, 0, 0, 1, 3),   # Csus2
    (2, "maj"):  (-1, -1, 0, 2, 3, 2),  # D
    (2, "min"):  (-1, -1, 0, 2, 3, 1),  # Dm
    (2, "7"):    (-1, -1, 0, 2, 1, 2),  # D7
    (2, "min7"): (-1, -1, 0, 2, 1, 1),  # Dm7
    (2, "sus4"): (-1, -1, 0, 2, 3, 3),  # Dsus4
    (2, "sus2"): (-1, -1, 0, 2, 3, 0),  # Dsus2
    (4, "maj"):  (0, 2, 2, 1, 0, 0),    # E
    (4, "min"):  (0, 2, 2, 0, 0, 0),    # Em
    (4, "7"):    (0, 2, 0, 1, 0, 0),    # E7
    (4, "min7"): (0, 2, 2, 0, 3, 0),    # Em7
    (4, "sus2"): (0, 2, 4, 4, 0, 0),    # Esus2
    (5, "maj"):  (1, 3, 3, 2, 1, 1),    # F (cejilla)
    (5, "maj7"): (-1, -1, 3, 2, 1, 0),  # Fmaj7
    (7, "maj"):  (3, 2, 0, 0, 0, 3),    # G
    (7, "7"):    (3, 2, 0, 0, 0, 1),    # G7
    (7, "maj7"): (3, 2, 0, 0, 0, 2),    # Gmaj7
    (7, "sus2"): (3, 0, 0, 0, 3, 3),    # Gsus2
    (9, "maj"):  (-1, 0, 2, 2, 2, 0),   # A
    (9, "min"):  (-1, 0, 2, 2, 1, 0),   # Am
    (9, "7"):    (-1, 0, 2, 0, 2, 0),   # A7
    (9, "min7"): (-1, 0, 2, 0, 1, 0),   # Am7
    (9, "sus4"): (-1, 0, 2, 2, 3, 0),   # Asus4
    (9, "sus2"): (-1, 0, 2, 2, 0, 0),   # Asus2
    (11, "min"): (-1, 2, 4, 4, 3, 2),   # Bm
    (11, "7"):   (-1, 2, 1, 2, 0, 2),   # B7
}

# Cejillas moviles: forma relativa a la cejilla, con raiz en 6a o 5a cuerda.
# None = cuerda no tocada. El primer valor no-None marca la posicion de cejilla.
BARRE_E: dict[str, tuple[int | None, ...]] = {   # raiz en 6a cuerda
    "maj":    (0, 2, 2, 1, 0, 0),
    "min":    (0, 2, 2, 0, 0, 0),
    "7":      (0, 2, 0, 1, 0, 0),
    "min7":   (0, 2, 0, 0, 0, 0),
    "maj7":   (0, 2, 1, 1, 0, None),
    "min7b5": (0, 1, 0, 0, None, None),
    "dim":    (0, 1, 2, 0, None, None),
    "sus4":   (0, 2, 2, 2, 0, 0),
    "5":      (0, 2, 2, None, None, None),
    "6":      (0, 2, 0, 1, 2, None),
    "9":      (0, 1, 0, 1, 1, None),
    "sus2":   (0, 2, 4, 4, 0, 0),
}
BARRE_A: dict[str, tuple[int | None, ...]] = {   # raiz en 5a cuerda
    "maj":    (None, 0, 2, 2, 2, 0),
    "min":    (None, 0, 2, 2, 1, 0),
    "7":      (None, 0, 2, 0, 2, 0),
    "min7":   (None, 0, 2, 0, 1, 0),
    "maj7":   (None, 0, 2, 1, 2, 0),
    "min7b5": (None, 0, 1, 0, 1, None),
    "sus4":   (None, 0, 2, 2, 3, 0),
    "dim7":   (None, 0, 1, 2, 1, None),
    "dim":    (None, 0, 1, 2, 1, None),
    "5":      (None, 0, 2, 2, None, None),
    "6":      (None, 0, 2, 2, 2, 2),
    "min6":   (None, 0, 2, 2, 1, 2),
    "9":      (None, 0, 1, 0, 1, 0),
    "add9":   (None, 0, 2, 4, 2, 0),
    "sus2":   (None, 0, 2, 2, 0, 0),
}


@dataclass
class Fingering:
    """Digitacion concreta: traste por cuerda, de la 6a a la 1a. -1 = muteada."""
    frets: tuple[int, ...]
    base: int = 0           # traste de la cejilla (0 = posicion abierta)
    label: str = ""
    shape: str = "open"     # open | barre-E | barre-A

    @property
    def is_open(self) -> bool:
        return self.base == 0

    def max_fret(self) -> int:
        played = [f for f in self.frets if f > 0]
        return max(played) if played else 0


def fingering(chord: Chord, capo: int = 0, flats: bool = False) -> Fingering | None:
    """Mejor digitacion para un acorde. Prefiere posicion abierta, luego cejilla
    en 6a cuerda, luego en 5a, eligiendo la que caiga en el traste mas bajo.

    `capo` implementa el feature premium #6: con cejilla en el traste N, el
    acorde que suena como X se digita como la forma de (X - N).
    """
    shown = Chord(root=(chord.root - capo) % 12, quality=chord.quality)
    label = chord.name(flats)

    key = (shown.root, shown.quality)
    if key in OPEN_SHAPES:
        return Fingering(frets=OPEN_SHAPES[key], base=0, label=label, shape="open")

    candidates: list[Fingering] = []
    for shape_name, table, open_string in (
        ("barre-E", BARRE_E, 0),  # raiz en 6a cuerda (E)
        ("barre-A", BARRE_A, 1),  # raiz en 5a cuerda (A)
    ):
        rel = table.get(shown.quality)
        if rel is None:
            continue
        # Traste donde cae la fundamental en esa cuerda.
        root_pc = STD_TUNING[open_string] % 12
        base = (shown.root - root_pc) % 12
        if base == 0:
            base = 12  # evita la "cejilla en traste 0", que no es una forma real
        frets = tuple(-1 if r is None else base + r for r in rel)
        cand = Fingering(frets=frets, base=base, label=label, shape=shape_name)
        if cand.max_fret() <= 15:
            candidates.append(cand)

    if not candidates:
        return None
    return min(candidates, key=lambda f: (f.base, f.shape != "barre-E"))


def diagram(f: Fingering, width: int = 5) -> list[str]:
    """Diagrama ASCII del acorde. Cuerdas en columnas, 6a a la izquierda."""
    lines = []
    header = "".join("x" if fr < 0 else ("o" if fr == 0 else " ") for fr in f.frets)
    lines.append(f"  {header}   {f.label}")
    start = f.base if f.base > 0 else 1
    for i in range(width):
        fret_no = start + i
        row = ""
        for fr in f.frets:
            row += "*" if fr == fret_no else "|"
        marker = f"{fret_no:>2}" if (f.base > 0 and i == 0) else "  "
        lines.append(f"{marker} {row}")
    return lines


def tab_block(f: Fingering) -> list[str]:
    """El mismo acorde como tablatura de 6 lineas (1a cuerda arriba, convencion)."""
    names = ["e", "B", "G", "D", "A", "E"]
    out = []
    for i, name in enumerate(names):
        fr = f.frets[5 - i]  # frets va de 6a a 1a; la tab se imprime al revés
        cell = "x" if fr < 0 else str(fr)
        out.append(f"{name}|-{cell:<2}-")
    return out
