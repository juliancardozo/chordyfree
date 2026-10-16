"""Capa de teoria musical: tonalidad, funcion armonica, pedales y simplificacion.

Esta capa corrige al reconocedor acustico usando lo que la musica permite. El
reconocedor decide beat a beat mirando solo el croma; aca se mira la cancion
entera y se aplica lo que sabemos de armonia tonal: que acordes pertenecen a una
tonalidad, cuales son prestamos reconocibles, y cuando el audio simplemente no
define si un acorde es mayor o menor.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .theory import FLAT, PITCH, QUALITIES, SHARP, Chord

# Grados de la escala, en semitonos desde la tonica.
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)

# Triadas diatonicas y su cifrado romano.
MAJOR_DIATONIC = {0: ("I", "maj"), 2: ("ii", "min"), 4: ("iii", "min"),
                  5: ("IV", "maj"), 7: ("V", "maj"), 9: ("vi", "min"),
                  11: ("vii", "dim")}
MINOR_DIATONIC = {0: ("i", "min"), 2: ("ii", "dim"), 3: ("III", "maj"),
                  5: ("iv", "min"), 7: ("v", "min"), 8: ("VI", "maj"),
                  10: ("VII", "maj")}

# Prestamos del modo paralelo, muy frecuentes en musica popular.
BORROWED_IN_MAJOR = {3: "bIII", 5: "iv", 8: "bVI", 10: "bVII", 0: "i"}
BORROWED_IN_MINOR = {4: "III+", 5: "IV", 9: "VI", 11: "vii"}

# Perfiles de Krumhansl-Schmuckler: cuanto "pesa" cada grado en cada modo.
KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52,
                     5.19, 2.39, 3.66, 2.29, 2.88])
KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54,
                     4.75, 3.98, 2.69, 3.34, 3.17])

# Tonalidades comodas en guitarra, por cantidad de acordes en posicion abierta.
EASY_KEYS = {7: "G", 0: "C", 2: "D", 9: "A", 4: "E"}


@dataclass
class Key:
    tonic: int
    mode: str              # "major" | "minor"
    confidence: float = 0.0
    alternatives: list[str] = field(default_factory=list)

    @property
    def flats(self) -> bool:
        name = SHARP[self.tonic]
        rel = name if self.mode == "major" else SHARP[(self.tonic + 3) % 12]
        return rel in {"F", "Bb", "Eb", "Ab", "Db", "Gb"}

    def name(self) -> str:
        names = FLAT if self.flats else SHARP
        return f"{names[self.tonic]} {'mayor' if self.mode == 'major' else 'menor'}"

    def scale(self) -> tuple[int, ...]:
        base = MAJOR_SCALE if self.mode == "major" else MINOR_SCALE
        return tuple((self.tonic + d) % 12 for d in base)


def estimate_key(spans, chroma: np.ndarray | None = None) -> Key:
    """Tonalidad combinando distribucion de alturas con evidencia de tonica.

    El perfil de Krumhansl por si solo no distingue una tonalidad de su relativa
    (La menor y Do mayor tienen exactamente las mismas notas). Por eso se suma
    evidencia de cual acorde funciona como reposo: el primero, el ultimo y el
    mas frecuente de la cancion. Sin esto, un tema en La menor se reporta como
    Do mayor — que fue exactamente lo que paso con el backing track de prueba.
    """
    weight = np.zeros(12)
    for s in spans:
        if s.chord:
            dur = max(0.0, s.end - s.start)
            for pc in s.chord.pitches():
                weight[pc] += dur
    if weight.sum() == 0:
        return Key(tonic=0, mode="major", confidence=0.0)
    weight = weight / weight.sum()

    chord_roots = [s.chord.root for s in spans if s.chord]
    chord_dur: dict[int, float] = {}
    for s in spans:
        if s.chord:
            chord_dur[s.chord.root] = chord_dur.get(s.chord.root, 0.0) + (s.end - s.start)

    first_root = chord_roots[0] if chord_roots else None
    last_root = chord_roots[-1] if chord_roots else None
    top_root = max(chord_dur, key=chord_dur.get) if chord_dur else None

    # La calidad del acorde de reposo decide mayor vs menor mejor que el perfil.
    quality_of: dict[int, str] = {}
    for s in spans:
        if s.chord and s.chord.root not in quality_of:
            quality_of[s.chord.root] = s.chord.quality

    total_dur = sum(max(0.0, s.end - s.start) for s in spans if s.chord) or 1.0

    def diatonic_coverage(tonic: int, mode: str) -> float:
        """Fraccion del tiempo de la cancion que cabe en la escala candidata.

        Es el criterio mas decisivo y el mas barato de verificar: si una
        tonalidad exige una nota que la cancion practicamente no toca, no es la
        tonalidad. Sin este termino, el estimador elegia Si mayor para un tema
        en Do# menor solo porque el acorde de Si era el mas frecuente — pero Si
        mayor exige La#, y la cancion tocaba La natural 60 veces mas.
        """
        base = MAJOR_SCALE if mode == "major" else MINOR_SCALE
        scale = {(tonic + d) % 12 for d in base}
        acc = 0.0
        for sp in spans:
            if not sp.chord:
                continue
            dur = max(0.0, sp.end - sp.start)
            outside = len(set(sp.chord.pitches()) - scale)
            acc += dur * (1.0 if outside == 0 else 0.45 if outside == 1 else 0.0)
        return acc / total_dur

    # Dos etapas, y el orden importa. La cobertura diatonica no se suma al
    # puntaje: se usa ANTES, para descartar. Sumarla no alcanzaba porque las
    # diferencias de correlacion de Krumhansl (~0.5) aplastan a las de cobertura
    # (~0.05), y el perfil premia a la tonalidad cuya tonica mas se toca — que
    # en un tema que vive sobre el V o el VII es la respuesta equivocada.
    coverage = {(t, m): diatonic_coverage(t, m)
                for t in range(12) for m in ("major", "minor")}
    best_cov = max(coverage.values()) if coverage else 0.0

    results: list[tuple[float, int, str]] = []
    for tonic in range(12):
        rotated = np.roll(weight, -tonic)
        for mode, profile in (("major", KS_MAJOR), ("minor", KS_MINOR)):
            r = float(np.corrcoef(rotated, profile)[0, 1])
            if np.isnan(r):
                r = 0.0
            # Una tonalidad que exige una nota que la cancion no toca queda
            # fuera de carrera, por bien que correlacione su perfil.
            deficit = best_cov - coverage[(tonic, mode)]
            if deficit > 0.05:
                r -= 2.0 * deficit
            # Evidencia de tonica: el reposo al final y el arranque pesan mas
            # que la frecuencia. Un tema puede pasar la mayor parte del tiempo
            # en el V o el VII sin que eso los vuelva tonica.
            if last_root == tonic:
                r += 0.16
            if first_root == tonic:
                r += 0.14
            if top_root == tonic:
                r += 0.03
            # Coherencia entre el modo y la calidad del acorde de tonica.
            q = quality_of.get(tonic, "")
            if mode == "major" and q in ("maj", "maj7", "7", "6", "sus4", "sus2"):
                r += 0.10
            elif mode == "minor" and q in ("min", "min7", "min6", "min9"):
                r += 0.10
            elif mode == "major" and q.startswith("min"):
                r -= 0.08
            elif mode == "minor" and q in ("maj", "maj7"):
                r -= 0.08
            results.append((r, tonic, mode))

    results.sort(reverse=True)
    best, second = results[0], results[1]
    margin = best[0] - second[0]
    names = []
    for r, t, m in results[1:4]:
        names.append(f"{SHARP[t]} {'mayor' if m == 'major' else 'menor'}")
    return Key(tonic=best[1], mode=best[2],
               confidence=float(min(1.0, max(0.0, best[0]))),
               alternatives=names)


def key_prior(key: Key, labels) -> np.ndarray:
    """Prior sobre los estados del decodificador, condicionado a la tonalidad.

    Premia los acordes diatonicos, tolera los prestamos y dominantes secundarias
    que la musica popular usa de verdad, y penaliza el resto. Esto es lo que
    convierte un `Am` espurio en el `A` correcto cuando la cancion esta en La
    mayor, sin prohibir el `Am` cuando realmente suena.
    """
    scale = set(key.scale())
    diatonic = MAJOR_DIATONIC if key.mode == "major" else MINOR_DIATONIC
    borrowed = BORROWED_IN_MAJOR if key.mode == "major" else BORROWED_IN_MINOR

    prior = np.zeros(len(labels), dtype=np.float32)
    for i, c in enumerate(labels):
        if c is None:
            continue
        degree = (c.root - key.tonic) % 12
        notes = set(c.pitches())
        outside = len(notes - scale)

        if degree in diatonic:
            _, want = diatonic[degree]
            # Premio pleno si ademas la calidad es la que corresponde al grado.
            if c.quality == want or c.quality.startswith(want):
                prior[i] += 0.55
            else:
                prior[i] += 0.15
        elif degree in borrowed:
            prior[i] += 0.10          # prestamo reconocible: ni premio ni castigo
        else:
            prior[i] -= 0.25

        # Dominante secundaria: acorde mayor/dominante una quinta sobre un grado.
        target = (c.root + 5) % 12
        if c.quality in ("maj", "7") and (target - key.tonic) % 12 in diatonic:
            prior[i] += 0.18

        # Cada nota ajena a la escala resta, proporcionalmente.
        prior[i] -= 0.12 * outside

    return prior


def third_strength(chroma_row: np.ndarray, root: int) -> tuple[float, float]:
    """Energia de la tercera mayor y de la menor sobre una fundamental dada."""
    return float(chroma_row[(root + 4) % 12]), float(chroma_row[(root + 3) % 12])


def mark_ambiguous_thirds(spans, chroma: np.ndarray, beats: np.ndarray,
                          ratio: float = 1.35) -> list[dict]:
    """Marca los acordes cuya tercera no esta definida en el audio.

    Un acorde sin tercera (power chord, voicing cuartal, pedal con cuerdas al
    aire) es genuinamente ambiguo entre mayor y menor: no es un error del
    detector, es que la musica no lo define. Reportarlo como "sin tercera" es
    mas honesto y mas util que forzar una decision.
    """
    out: list[dict] = []
    if chroma.shape[0] == 0 or len(beats) < 2:
        return out

    for s in spans:
        if not s.chord:
            continue
        i0 = int(np.searchsorted(beats, s.start, side="right") - 1)
        i1 = int(np.searchsorted(beats, s.end, side="left"))
        i0 = max(0, min(i0, chroma.shape[0] - 1))
        i1 = max(i0 + 1, min(i1, chroma.shape[0]))
        seg = chroma[i0:i1]
        if seg.size == 0:
            continue
        row = seg.mean(axis=0)
        root_e = float(row[s.chord.root % 12])
        maj3, min3 = third_strength(row, s.chord.root)
        strongest = max(maj3, min3)
        weakest = min(maj3, min3)
        # Tercera debil respecto de la fundamental, o las dos terceras parejas.
        no_third = strongest < 0.45 * root_e
        ambiguous = weakest > 0 and (strongest / max(weakest, 1e-6)) < ratio
        if no_third or ambiguous:
            out.append({
                "start": s.start, "end": s.end, "label": s.chord.name(),
                "razon": "sin tercera audible" if no_third else "tercera ambigua",
                "maj3": round(maj3, 3), "min3": round(min3, 3),
                "root": round(root_e, 3),
            })
    return out


def detect_pedal(spans, bass: np.ndarray, beats: np.ndarray,
                 min_fraction: float = 0.55) -> dict | None:
    """Detecta un pedal: una nota del bajo que se sostiene bajo varios acordes.

    Es un rasgo de arreglo muy audible y cambia como se toca la cancion: con
    pedal, la mano izquierda mantiene una nota fija y solo cambian las voces de
    arriba. Tambien explica acordes "raros" que en realidad son inversiones.
    """
    if bass.shape[0] == 0:
        return None
    dominant = np.argmax(bass, axis=1)
    counts = np.bincount(dominant, minlength=12).astype(float)
    counts /= counts.sum()
    pc = int(np.argmax(counts))
    frac = float(counts[pc])
    if frac < min_fraction:
        return None
    distintos = len({s.chord.root for s in spans if s.chord})
    if distintos < 3:
        return None
    return {"nota": SHARP[pc], "pitch_class": pc, "fraccion": round(frac, 2)}


def roman_numeral(chord: Chord, key: Key) -> str:
    """Cifrado romano del acorde en la tonalidad, con prestamos y secundarias."""
    degree = (chord.root - key.tonic) % 12
    diatonic = MAJOR_DIATONIC if key.mode == "major" else MINOR_DIATONIC
    borrowed = BORROWED_IN_MAJOR if key.mode == "major" else BORROWED_IN_MINOR

    suffix = ""
    if chord.quality in ("7",):
        suffix = "7"
    elif chord.quality in ("maj7",):
        suffix = "maj7"
    elif chord.quality in ("min7",):
        suffix = "7"
    elif chord.quality in ("sus4", "sus2"):
        suffix = chord.quality
    elif chord.quality == "min7b5":
        suffix = "ø7"
    elif chord.quality == "dim7":
        suffix = "°7"

    if degree in diatonic:
        num, want = diatonic[degree]
        if chord.quality.startswith("min") and num.isupper():
            num = num.lower()
        elif chord.quality in ("maj", "maj7", "7", "6") and num.islower():
            num = num.upper()
        return num + suffix

    # Dominante secundaria: resuelve una quinta abajo, a un grado de la escala.
    if chord.quality in ("maj", "7"):
        target = (chord.root + 5) % 12
        tdeg = (target - key.tonic) % 12
        if tdeg in diatonic:
            return f"V{suffix or '7'}/{diatonic[tdeg][0]}"

    if degree in borrowed:
        return borrowed[degree] + suffix

    names = FLAT if key.flats else SHARP
    return f"({names[chord.root]})"


def simplify_chord(chord: Chord, key: Key) -> Chord:
    """Reduce un acorde a su forma mas simple tocable sin perder su funcion.

    Las septimas y novenas se bajan a triada; los sus se resuelven a la triada
    que la tonalidad implica. Es la base de la "version simplificada".
    """
    q = chord.quality
    if q in ("maj", "min"):
        simple = q
    elif q in ("maj7", "7", "6", "9", "maj9", "add9", "5"):
        simple = "maj"
    elif q in ("min7", "min6", "min9", "minmaj7"):
        simple = "min"
    elif q in ("dim", "dim7", "min7b5"):
        simple = "min"          # mas tocable y suena aceptable en contexto popular
    elif q in ("sus4", "sus2"):
        # Un sus se resuelve segun lo que la tonalidad pida para ese grado.
        degree = (chord.root - key.tonic) % 12
        diatonic = MAJOR_DIATONIC if key.mode == "major" else MINOR_DIATONIC
        simple = diatonic[degree][1] if degree in diatonic else "maj"
        if simple == "dim":
            simple = "min"
    else:
        simple = "maj"
    return Chord(root=chord.root, quality=simple)


def suggest_capo(key: Key) -> dict | None:
    """Busca una cejilla que lleve la cancion a una tonalidad comoda en guitarra.

    Toca lo mismo que suena, pero con formas abiertas en vez de cejillas.
    """
    best = None
    for capo in range(1, 8):
        shown = (key.tonic - capo) % 12
        if shown in EASY_KEYS:
            # El modo menor es comodo en Am, Em, Dm.
            if key.mode == "minor" and shown not in (9, 4, 2):
                continue
            score = capo  # cuanto mas baja la cejilla, mejor
            if best is None or score < best["score"]:
                best = {"capo": capo, "forma": EASY_KEYS[shown],
                        "score": score, "shown_tonic": shown}
    if best:
        best.pop("score")
    return best


def section_progression(spans, bars_per_phrase: int = 4) -> list[dict]:
    """Agrupa la cancion en frases y detecta cual se repite (patron ciclico)."""
    by_bar: dict[int, list[str]] = {}
    for s in spans:
        if s.chord:
            by_bar.setdefault(s.bar, [])
            name = s.chord.name()
            if not by_bar[s.bar] or by_bar[s.bar][-1] != name:
                by_bar[s.bar].append(name)

    bars = sorted(by_bar)
    phrases: list[dict] = []
    for i in range(0, len(bars), bars_per_phrase):
        chunk = bars[i:i + bars_per_phrase]
        seq = " | ".join(" ".join(by_bar[b]) for b in chunk)
        phrases.append({"desde": chunk[0], "hasta": chunk[-1], "seq": seq})

    counts: dict[str, int] = {}
    for p in phrases:
        counts[p["seq"]] = counts.get(p["seq"], 0) + 1
    for p in phrases:
        p["repeticiones"] = counts[p["seq"]]
    return phrases
