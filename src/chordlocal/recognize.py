"""Reconocimiento de acordes sincronizado al beat.

Enfoque: separacion armonico/percusiva -> croma CQT -> agregacion por beat ->
comparacion contra plantillas de acorde -> Viterbi para suavizar la secuencia.

El paso por Viterbi es lo que diferencia una deteccion usable de una llena de
ruido: sin el, el croma oscila entre acordes vecinos varias veces por compas.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .theory import QUALITIES, SHARP, Chord

# Vocabulario de acordes. Mas calidades no es mejor: cada una agrega un estado
# que puede robarle probabilidad al correcto. Este set cubre el repertorio
# popular (lo mismo que muestra Chordify) sin diluir la deteccion.
VOCAB_QUALITIES = ("maj", "min", "7", "min7", "maj7", "min7b5", "dim", "sus4", "sus2")

# Peso de cada grado dentro de la plantilla. La fundamental y la tercera son las
# que definen el acorde; la quinta es ambigua (la comparten mayor y menor) y la
# septima es mas debil en la mezcla, asi que pesan menos.
DEGREE_WEIGHT = {0: 1.0, 3: 1.0, 4: 1.0, 7: 0.55, 10: 0.7, 11: 0.7, 6: 0.8, 8: 0.8,
                 2: 0.7, 5: 0.7, 9: 0.7}

# Prior por calidad, en unidades de log-verosimilitud. Sin esto, los acordes de
# cuatro notas se comen a las triadas que contienen: Cmaj7 (C-E-G-B) explica a la
# vez C (C-E-G) y Em (E-G-B), asi que gana a ambos aunque ninguno sea el correcto.
# Las triadas son ampliamente las mas frecuentes en el repertorio popular.
QUALITY_PRIOR = {
    "maj": 0.0, "min": 0.0,
    "7": -0.35, "min7": -0.35,
    "sus4": -0.55, "sus2": -0.65,
    "maj7": -0.75, "min7b5": -0.9, "dim": -0.9,
}


@dataclass
class ChordSpan:
    """Un acorde con su tramo temporal y los beats que ocupa."""
    start: float
    end: float
    chord: Chord | None
    bar: int = 0
    beat: int = 0
    confidence: float = 0.0

    @property
    def label(self) -> str:
        return self.chord.name() if self.chord else "N"


def build_templates() -> tuple[np.ndarray, list[Chord | None]]:
    """Matriz (n_estados, 12) de plantillas normalizadas + los acordes que representan.

    El ultimo estado es "sin acorde" (silencio o pasaje no armonico): plantilla
    plana, de modo que gana solo cuando el croma no se parece a ningun acorde.
    """
    templates: list[np.ndarray] = []
    labels: list[Chord | None] = []

    for root in range(12):
        for quality in VOCAB_QUALITIES:
            intervals = QUALITIES[quality]
            vec = np.zeros(12, dtype=np.float32)
            for iv in intervals:
                vec[(root + iv) % 12] += DEGREE_WEIGHT.get(iv % 12, 0.6)
            # Segundo armonico de cada nota (una quinta arriba) aporta un poco:
            # ayuda a que el croma real de instrumentos con muchos armonicos
            # siga pareciendose a la plantilla.
            for iv in intervals:
                vec[(root + iv + 7) % 12] += 0.12
            n = np.linalg.norm(vec)
            templates.append(vec / n if n else vec)
            labels.append(Chord(root=root, quality=quality))

    flat = np.full(12, 1.0 / np.sqrt(12), dtype=np.float32)
    templates.append(flat)
    labels.append(None)
    return np.vstack(templates), labels


def beat_synchronous_chroma(
    y: np.ndarray, sr: int, beat_times: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Croma promediado entre beat y beat. Devuelve (croma por beat, croma de bajos).

    Trabajar por beat (y no por frame) es lo que alinea los acordes al compas,
    que es exactamente la grilla que muestra Chordify.

    Nota: NO se comprime el croma logaritmicamente. Probado y medido: log1p sobre
    la magnitud del CQT levanta el piso de ruido respecto de los picos y deja el
    vector casi plano (picudez 4.8 -> 1.8), con lo cual el estado "sin acorde"
    gana siempre. La separacion armonico/percusiva, en cambio, si mejora el
    contraste y se mantiene.
    """
    import librosa

    # Quitar la percusion antes de calcular croma: los golpes de batería son
    # banda ancha y contaminan todas las clases de altura por igual.
    y_harm = librosa.effects.harmonic(y, margin=3.0)

    # Croma de rango completo para la armonia.
    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr, hop_length=512)

    # Croma restringido al registro grave: pista del bajo, util para desempatar
    # acordes que comparten notas.
    bass_chroma = librosa.feature.chroma_cqt(
        y=y_harm, sr=sr, hop_length=512,
        fmin=librosa.note_to_hz("C2"), n_octaves=2)

    frames = librosa.time_to_frames(beat_times, sr=sr, hop_length=512)
    frames = np.clip(frames, 0, chroma.shape[1] - 1)

    def aggregate(mat: np.ndarray) -> np.ndarray:
        out = np.zeros((len(frames) - 1, 12), dtype=np.float32)
        for i in range(len(frames) - 1):
            a, b = frames[i], max(frames[i] + 1, frames[i + 1])
            seg = mat[:, a:b]
            # Mediana, no media: resiste el ataque del acorde y los adornos.
            out[i] = np.median(seg, axis=1) if seg.size else 0.0
            n = np.linalg.norm(out[i])
            if n:
                out[i] /= n
        return out

    return aggregate(chroma), aggregate(bass_chroma)


def viterbi_decode(
    scores: np.ndarray,
    priors: np.ndarray | None = None,
    change_penalty: float = 1.6,
    no_chord_penalty: float = 1.4,
    sharpness: float = 6.0,
) -> np.ndarray:
    """Camino mas probable sobre la matriz (n_beats, n_estados) de similitudes.

    `change_penalty` es el costo ABSOLUTO (en log) de cambiar de acorde entre un
    beat y el siguiente. Deliberadamente no se divide por el numero de estados:
    hacerlo ataba el costo del cambio al tamaño del vocabulario, de modo que
    agregar calidades volvia imposible cualquier transicion y la secuencia se
    colapsaba en un solo acorde que fuera superconjunto de los demas.

    Subirlo da secuencias mas estables; bajarlo las hace mas reactivas.
    """
    n_beats, n_states = scores.shape
    if n_beats == 0:
        return np.zeros(0, dtype=int)

    # Similitud coseno (0..1) -> log-verosimilitud. El exponente agudiza la
    # diferencia entre el mejor candidato y el resto.
    emission = np.log(np.clip(scores, 1e-6, None)) * sharpness
    if priors is not None:
        emission = emission + priors[None, :]
    emission[:, -1] -= no_chord_penalty  # "sin acorde" debe ganarse su lugar

    delta = emission[0].copy()
    backptr = np.zeros((n_beats, n_states), dtype=np.int32)

    for t in range(1, n_beats):
        # Quedarse (costo 0) vs. venir del mejor estado anterior (costo fijo).
        # Con penalizacion uniforme alcanza comparar contra el maximo global.
        best_prev = int(np.argmax(delta))
        move_score = delta[best_prev] - change_penalty
        stay_score = delta

        stay_wins = stay_score >= move_score
        delta = np.where(stay_wins, stay_score, move_score) + emission[t]
        backptr[t] = np.where(stay_wins, np.arange(n_states), best_prev)

    path = np.zeros(n_beats, dtype=int)
    path[-1] = int(np.argmax(delta))
    for t in range(n_beats - 1, 0, -1):
        path[t - 1] = backptr[t][path[t]]
    return path


def decode(
    chroma: np.ndarray,
    bass: np.ndarray,
    beat_times: np.ndarray,
    downbeats: np.ndarray | None = None,
    key_prior: np.ndarray | None = None,
) -> list[ChordSpan]:
    """Decide la secuencia de acordes a partir del croma ya calculado.

    Separada de `recognize` a proposito: permite una SEGUNDA pasada con un prior
    de tonalidad (una vez que sabemos la tonalidad) sin volver a procesar audio.
    Esa segunda pasada es instantanea.
    """
    templates, labels = build_templates()
    if chroma.shape[0] == 0:
        return []

    scores = chroma @ templates.T

    roots = np.array([(c.root if c else -1) for c in labels])
    for s in range(len(labels)):
        if roots[s] >= 0:
            scores[:, s] += 0.10 * bass[:, roots[s]]

    priors = np.array([
        (QUALITY_PRIOR.get(c.quality, -0.5) if c else 0.0) for c in labels
    ], dtype=np.float32)
    if key_prior is not None:
        priors = priors + key_prior

    path = viterbi_decode(np.clip(scores, 1e-6, None), priors=priors)

    db = set(np.round(downbeats, 3).tolist()) if downbeats is not None else set()
    bar, beat_in_bar = 1, 1
    spans: list[ChordSpan] = []
    for i, state in enumerate(path):
        t0, t1 = float(beat_times[i]), float(beat_times[i + 1])
        if db and round(t0, 3) in db:
            if i > 0:
                bar += 1
            beat_in_bar = 1
        chord = labels[state]
        spans.append(ChordSpan(start=t0, end=t1, chord=chord, bar=bar,
                               beat=beat_in_bar, confidence=float(scores[i, state])))
        beat_in_bar += 1
    return merge_spans(spans)


def state_labels() -> list[Chord | None]:
    """Los acordes que representa cada estado del decodificador, en orden."""
    return build_templates()[1]


def recognize(
    y: np.ndarray, sr: int, beat_times: np.ndarray, downbeats: np.ndarray | None = None
) -> tuple[list[ChordSpan], np.ndarray, np.ndarray]:
    """Pipeline de acordes. Devuelve (tramos, croma por beat, croma de bajos)."""
    if len(beat_times) < 2:
        return [], np.zeros((0, 12)), np.zeros((0, 12))

    chroma, bass = beat_synchronous_chroma(y, sr, beat_times)
    spans = decode(chroma, bass, beat_times, downbeats)
    return spans, chroma, bass


def merge_spans(spans: list[ChordSpan]) -> list[ChordSpan]:
    """Une beats consecutivos con el mismo acorde en un solo tramo."""
    if not spans:
        return []
    out = [spans[0]]
    for s in spans[1:]:
        prev = out[-1]
        same = (prev.chord is None and s.chord is None) or (
            prev.chord and s.chord
            and prev.chord.root == s.chord.root
            and prev.chord.quality == s.chord.quality
        )
        if same:
            prev.end = s.end
            prev.confidence = max(prev.confidence, s.confidence)
        else:
            out.append(s)
    return out


# ----------------------------------------------------------------------------
# Tonalidad, derivada de los acordes detectados
# ----------------------------------------------------------------------------

# Grados de la escala presentes en cada modo, con el peso de su importancia
# funcional (tonica y dominante pesan mas que el resto).
MAJOR_DEGREES = {0: 3.0, 2: 1.0, 4: 1.5, 5: 2.0, 7: 2.5, 9: 1.5, 11: 0.5}
MINOR_DEGREES = {0: 3.0, 2: 0.8, 3: 1.5, 5: 2.0, 7: 2.5, 8: 1.2, 10: 1.2}


def detect_key(spans: list[ChordSpan]) -> tuple[str, str, float]:
    """Deduce la tonalidad del repertorio de acordes, ponderado por duracion.

    Trabajar sobre los acordes (y no sobre el croma crudo) da una respuesta mas
    estable, porque el acorde ya es una decision tomada sobre varios beats.
    """
    weight = np.zeros(12)
    for s in spans:
        if s.chord:
            weight[s.chord.root] += max(0.0, s.end - s.start)
    if weight.sum() == 0:
        return "C", "major", 0.0
    weight /= weight.sum()

    best = ("C", "major", -1.0)
    for tonic in range(12):
        for mode, degrees in (("major", MAJOR_DEGREES), ("minor", MINOR_DEGREES)):
            score = sum(w * weight[(tonic + d) % 12] for d, w in degrees.items())
            if score > best[2]:
                best = (SHARP[tonic], mode, score)

    tonic, mode, raw = best
    # Normalizamos a 0..1 contra el maximo teorico para poder reportar confianza.
    return tonic, mode, float(min(1.0, raw / 2.2))
