"""Transcripcion de notas (basic-pitch) y conversion a tablatura de guitarra.

El paso dificil no es detectar las notas, es decidir *donde* tocarlas: una misma
nota existe en varias cuerdas. Se resuelve con programacion dinamica sobre la
posicion de la mano, minimizando desplazamientos — que es como un guitarrista
elige la digitacion en la practica.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .theory import STD_TUNING

MAX_FRET = 17
HAND_SPAN = 4        # trastes que la mano abarca sin desplazarse
OPEN_STRING_BONUS = 0.8   # las cuerdas al aire son "gratis", no requieren mano


@dataclass
class Note:
    start: float
    end: float
    midi: int
    velocity: int = 80
    confidence: float = 0.0


@dataclass
class TabNote:
    start: float
    end: float
    string: int   # 0 = 6a cuerda (mas grave) .. 5 = 1a
    fret: int
    midi: int


def _model_path():
    """Ruta al modelo de basic-pitch, forzando ONNX sobre CoreML.

    En macOS basic-pitch elige por defecto el modelo CoreML (.mlpackage), cuya
    inferencia aborta con SIGFPE en este Mac Intel con Monterey. El mismo modelo
    en formato ONNX viene en el paquete y corre bien sobre onnxruntime/CPU.
    """
    from basic_pitch import ICASSP_2022_MODEL_PATH

    default = Path(str(ICASSP_2022_MODEL_PATH))
    onnx = default.parent / "nmp.onnx"
    if onnx.exists():
        return onnx
    return default


def transcribe_notes(
    wav: Path, onset_threshold: float = 0.5, frame_threshold: float = 0.3,
    min_note_len: float = 0.08,
) -> list[Note]:
    """Transcripcion polifonica con basic-pitch (ONNX, corre en CPU).

    Los umbrales por defecto son conservadores: preferimos perder alguna nota
    fantasma antes que llenar la tablatura de basura, porque una tablatura con
    notas inventadas es peor que una incompleta.
    """
    from basic_pitch.inference import predict

    _, _, note_events = predict(
        str(wav),
        model_or_model_path=_model_path(),
        onset_threshold=onset_threshold,
        frame_threshold=frame_threshold,
        minimum_note_length=min_note_len * 1000.0,
    )

    notes = [
        Note(start=float(n[0]), end=float(n[1]), midi=int(n[2]),
             confidence=float(n[3]) if len(n) > 3 else 0.0)
        for n in note_events
    ]
    notes.sort(key=lambda n: (n.start, n.midi))
    return notes


def playable_range(notes: list[Note]) -> list[Note]:
    """Descarta lo que la guitarra no puede tocar en afinacion estandar.

    Las notas graves fuera de rango se transportan una octava arriba en vez de
    perderse: suelen ser la linea de bajo, que es util tener en la tablatura.
    """
    lowest, highest = STD_TUNING[0], STD_TUNING[5] + MAX_FRET
    out: list[Note] = []
    for n in notes:
        midi = n.midi
        while midi < lowest:
            midi += 12
        if midi > highest:
            continue
        out.append(Note(start=n.start, end=n.end, midi=midi,
                        velocity=n.velocity, confidence=n.confidence))
    return out


def _slices(notes: list[Note], tol: float = 0.06) -> list[list[Note]]:
    """Agrupa notas que suenan juntas (dentro de `tol`) en un mismo evento."""
    groups: list[list[Note]] = []
    for n in notes:
        if groups and abs(n.start - groups[-1][0].start) <= tol:
            groups[-1].append(n)
        else:
            groups.append([n])
    return groups


def _assign_in_position(group: list[Note], pos: int) -> list[TabNote] | None:
    """Asigna las notas de un evento a cuerdas distintas dentro de una posicion.

    Devuelve None si no entra en esa posicion. Asigna primero las notas graves,
    que tienen menos cuerdas candidatas y por lo tanto menos libertad.
    """
    used: set[int] = set()
    out: list[TabNote] = []
    for n in sorted(group, key=lambda x: x.midi):
        best: tuple[float, int, int] | None = None
        for s in range(6):
            if s in used:
                continue
            fret = n.midi - STD_TUNING[s]
            if fret < 0 or fret > MAX_FRET:
                continue
            # Dentro de la posicion de la mano, o cuerda al aire.
            if fret != 0 and not (pos <= fret <= pos + HAND_SPAN):
                continue
            cost = 0.0 if fret == 0 else float(fret - pos)
            # Preferir cuerdas graves para notas graves: mejor sonido y digitacion.
            cost += abs(s - (n.midi - 40) / 8.0) * 0.1
            if best is None or cost < best[0]:
                best = (cost, s, fret)
        if best is None:
            return None
        _, s, fret = best
        used.add(s)
        out.append(TabNote(start=n.start, end=n.end, string=s, fret=fret, midi=n.midi))
    return out


def to_tab(notes: list[Note]) -> list[TabNote]:
    """Convierte notas en posiciones cuerda/traste minimizando el movimiento.

    DP sobre la posicion de la mano: el estado es el traste donde empieza la
    mano, el costo es cuanto hay que desplazarse mas que tan arriba se toca.
    """
    notes = playable_range(notes)
    groups = _slices(notes)
    if not groups:
        return []

    positions = list(range(0, MAX_FRET - HAND_SPAN + 1))

    # Para cada evento, que posiciones lo permiten y con que asignacion.
    feasible: list[dict[int, list[TabNote]]] = []
    for g in groups:
        opts = {}
        for pos in positions:
            a = _assign_in_position(g, pos)
            if a is not None:
                opts[pos] = a
        if not opts:
            # Evento intocable completo: lo partimos y conservamos lo que entre.
            for pos in positions:
                partial = [x for x in (
                    _assign_in_position([n], pos) for n in g) if x]
                if partial:
                    opts[pos] = [t for sub in partial for t in sub]
                    break
        feasible.append(opts)

    INF = float("inf")
    # cost[pos] = costo minimo acumulado terminando con la mano en `pos`
    cost = {p: (0.0 if p in feasible[0] else INF) for p in positions}
    for p in positions:
        if p in feasible[0]:
            cost[p] = p * 0.15  # arrancar cerca del clavijero es preferible
    back: list[dict[int, int]] = [{}]

    for i in range(1, len(feasible)):
        new_cost = {p: INF for p in positions}
        bp: dict[int, int] = {}
        for p in positions:
            if p not in feasible[i]:
                continue
            best = INF
            best_prev = p
            for q in positions:
                if cost[q] == INF:
                    continue
                # Desplazar la mano es el costo dominante; tocar agudo, secundario.
                c = cost[q] + abs(p - q) * 1.0 + p * 0.05
                if c < best:
                    best, best_prev = c, q
            if best < INF:
                new_cost[p] = best
                bp[p] = best_prev
        if all(v == INF for v in new_cost.values()):
            # Nada viable: reiniciamos desde cero en este evento.
            for p in positions:
                if p in feasible[i]:
                    new_cost[p] = p * 0.15
                    bp[p] = p
        cost = new_cost
        back.append(bp)

    end = min((p for p in positions if cost[p] < INF), key=lambda p: cost[p], default=None)
    if end is None:
        return []

    chosen = [0] * len(feasible)
    chosen[-1] = end
    for i in range(len(feasible) - 1, 0, -1):
        chosen[i - 1] = back[i].get(chosen[i], chosen[i])

    out: list[TabNote] = []
    for i, pos in enumerate(chosen):
        a = feasible[i].get(pos)
        if a is None and feasible[i]:
            a = next(iter(feasible[i].values()))
        if a:
            out.extend(a)
    out.sort(key=lambda t: (t.start, -t.string))
    return out
