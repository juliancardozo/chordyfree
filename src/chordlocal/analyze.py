"""Orquestador del analisis: audio -> analysis.json.

Esta es la etapa lenta y se corre UNA vez por cancion. Todo lo demas (transponer,
capo, loop, cambiar tempo, exportar) opera sobre el JSON resultante y es instantaneo.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .fetch import Source
from .recognize import ChordSpan, decode, recognize, state_labels
from .theory import key_uses_flats

ANALYSIS_VERSION = 2


@dataclass
class Analysis:
    source: dict
    key: dict
    tempo: dict
    beats: list[dict] = field(default_factory=list)
    chords: list[dict] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)
    tab: list[dict] = field(default_factory=list)
    timings: dict = field(default_factory=dict)
    armonia: dict = field(default_factory=dict)
    contraste: dict = field(default_factory=dict)
    simple: list[dict] = field(default_factory=list)
    chroma: list = field(default_factory=list)
    bass: list = field(default_factory=list)      # croma grave por beat
    version: int = ANALYSIS_VERSION

    def to_json(self) -> str:
        return json.dumps(self.__dict__, indent=2, ensure_ascii=False)

    @staticmethod
    def load(path: Path) -> "Analysis":
        d = json.loads(Path(path).read_text())
        d.pop("version", None)
        campos = set(Analysis.__dataclass_fields__)
        return Analysis(**{k: v for k, v in d.items() if k in campos},
                        version=ANALYSIS_VERSION)

    @property
    def flats(self) -> bool:
        return key_uses_flats(self.key.get("tonic", "C"), self.key.get("mode", "major"))


def track_beats(wav: Path) -> tuple[np.ndarray, np.ndarray]:
    """Beats y downbeats (en segundos) de un archivo, con tempo y compas coherentes."""
    import librosa
    from . import beats as bt

    y, sr = librosa.load(str(wav), sr=22050, mono=True)
    r = bt.track(y, sr)
    return r["beats"], r["downbeats"]


def detect_track_end(y: np.ndarray, sr: int, hop: int = 512) -> float | None:
    """Detecta si al final del audio empieza OTRA cancion, y devuelve donde cortar.

    Firma medida en una descarga real: fade-out (la mediana de los 4 s previos
    cae >15 dB bajo el nivel del tema), un silencio de >=0,35 s por debajo de
    -45 dB, y despues audio a volumen pleno hasta el final. Los cortes a mitad
    de tema (breaks, silencios dramaticos) no tienen fade previo, y por eso no
    se confunden.
    """
    import librosa

    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    if len(rms) < 100:
        return None
    t = hop / sr
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    rel = db - np.percentile(db, 95)
    dur = len(rel) * t
    sil = rel < -45
    i = int(dur * 0.75 / t)
    while i < len(sil):
        if not sil[i]:
            i += 1
            continue
        j = i
        while j < len(sil) and sil[j]:
            j += 1
        largo, resto = (j - i) * t, dur - j * t
        if largo >= 0.35 and 3.0 <= resto <= 60.0:
            antes = np.median(rel[max(0, i - int(4 / t)):i])
            despues = np.median(rel[j:])
            if antes < -15 and despues > -20:
                return round(i * t, 2)
        i = j
    return None


def estimate_bpm(beats: np.ndarray) -> float:
    """BPM a partir de la mediana del intervalo entre beats (resiste outliers)."""
    if len(beats) < 2:
        return 0.0
    iois = np.diff(beats)
    med = float(np.median(iois))
    return round(60.0 / med, 1) if med > 0 else 0.0


def infer_meter(beats: np.ndarray, downbeats: np.ndarray) -> str:
    """Compas a partir de cuantos beats caen entre downbeats consecutivos."""
    if len(downbeats) < 2 or len(beats) < 2:
        return "4/4"
    counts = []
    for i in range(len(downbeats) - 1):
        n = int(np.sum((beats >= downbeats[i] - 1e-6) & (beats < downbeats[i + 1] - 1e-6)))
        if 1 < n <= 12:
            counts.append(n)
    if not counts:
        return "4/4"
    mode = int(np.bincount(counts).argmax())
    return {3: "3/4", 4: "4/4", 6: "6/8", 5: "5/4", 2: "2/4"}.get(mode, "4/4")


def analyze(
    source: Source, with_notes: bool = True, with_web: bool = True, progress=print
) -> Analysis:
    """Analisis completo en seis etapas.

    1-3 son acusticas (audio -> beats -> acordes). 4 aplica teoria musical y
    re-decide. 5 contrasta con fuentes abiertas. 6 transcribe notas.
    """
    import librosa

    from . import harmony, verify
    from .theory import FLAT, SHARP

    timings: dict[str, float] = {}

    from . import beats as bt
    from .recognize import aggregate_by_beat, frame_chroma

    t0 = time.perf_counter()
    progress("  [1/6] cargando audio...")
    y, sr = librosa.load(str(source.wav), sr=22050, mono=True)
    fin = detect_track_end(y, sr)
    if fin:
        progress(f"        al final arranca otra pista: se analiza hasta {fin:.1f} s")
        y = y[:int(fin * sr)]
    timings["load"] = round(time.perf_counter() - t0, 1)

    t0 = time.perf_counter()
    progress("  [2/6] detectando beats y compases...")
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
    rb = bt.track(y, sr, onset=onset, onset_fps=sr / 512)
    beats, downbeats = rb["beats"], rb["downbeats"]
    timings["beats"] = round(time.perf_counter() - t0, 1)
    if len(beats) < 2:
        raise RuntimeError("no se detectaron beats; el audio puede estar en silencio")
    progress(f"        {rb['metro']} a {rb['bpm']} BPM ({rb['metodo']})")

    t0 = time.perf_counter()
    progress(f"  [3/6] reconociendo acordes sobre {len(beats)} beats...")
    fch, fbass = frame_chroma(y, sr)
    chroma = aggregate_by_beat(fch, beats, sr)
    bass = aggregate_by_beat(fbass, beats, sr)
    spans = decode(chroma, bass, beats, downbeats)
    timings["chords"] = round(time.perf_counter() - t0, 1)

    # --- Segunda pasada: teoria musical ---
    # La primera pasada decide beat a beat sin saber en que tonalidad esta la
    # cancion. Con la tonalidad ya estimada, re-decidimos con un prior que
    # premia lo diatonico y tolera prestamos. Es instantaneo: reusa el croma.
    t0 = time.perf_counter()
    progress("  [4/6] aplicando teoria musical (2a pasada)...")
    key = harmony.estimate_key(spans, chroma)
    labels = state_labels()
    prior = harmony.key_prior(key, labels)
    spans2 = decode(chroma, bass, beats, downbeats, key_prior=prior)
    # Si la segunda pasada destruye la cancion (colapsa a muy pocos acordes),
    # nos quedamos con la primera: el prior estaba mal informado.
    if len([x for x in spans2 if x.chord]) >= max(3, 0.35 * len([x for x in spans if x.chord])):
        spans = spans2
        key = harmony.estimate_key(spans, chroma)
    timings["armonia"] = round(time.perf_counter() - t0, 1)

    flats = key.flats
    tonic, mode = (FLAT if flats else SHARP)[key.tonic], key.mode
    conf = key.confidence

    # --- Contraste con fuentes abiertas de internet ---
    t0 = time.perf_counter()
    contraste: dict = {}
    if with_web:
        progress("  [5/6] contrastando con MusicBrainz / AcousticBrainz...")
        try:
            ev = verify.gather(source.title, source.artist)
            bpm_det = rb["bpm"]
            contraste = {
                "evidencia": ev.to_dict(),
                "tempo": verify.reconcile_tempo(bpm_det, ev.bpm),
                "tonalidad": verify.reconcile_key(tonic, mode, ev.tonica, ev.modo),
            }
        except Exception as e:
            contraste = {"error": str(e)}
    else:
        progress("  [5/6] contraste con internet omitido (--sin-internet)")
    timings["contraste"] = round(time.perf_counter() - t0, 1)

    notes_out: list[dict] = []
    tab_out: list[dict] = []
    if with_notes:
        t0 = time.perf_counter()
        progress("  [6/6] transcribiendo notas para tablatura...")
        from .transcribe import to_tab, transcribe_notes
        notes = transcribe_notes(source.wav)
        if fin:
            notes = [n for n in notes if n.start < fin]
        tabs = to_tab(notes)
        notes_out = [{"start": round(n.start, 3), "end": round(n.end, 3),
                      "midi": n.midi, "confidence": round(n.confidence, 3)} for n in notes]
        tab_out = [{"start": round(t.start, 3), "end": round(t.end, 3),
                    "string": t.string, "fret": t.fret, "midi": t.midi} for t in tabs]
        timings["notes"] = round(time.perf_counter() - t0, 1)
    else:
        progress("  [6/6] transcripcion de notas omitida (--no-notes)")

    downbeat_set = set(np.round(downbeats, 3).tolist())
    beats_out = []
    bar = 0
    for i, t in enumerate(beats):
        is_db = round(float(t), 3) in downbeat_set
        if is_db:
            bar += 1
        beats_out.append({"t": round(float(t), 3), "downbeat": is_db, "bar": max(bar, 1)})

    # --- Reporte armonico ---
    pedal = harmony.detect_pedal(spans, bass, beats)
    ambiguos = harmony.mark_ambiguous_thirds(spans, chroma, beats)
    capo = harmony.suggest_capo(key)
    frases = harmony.section_progression(spans)
    grados = [
        {"label": sp.chord.name(flats), "romano": harmony.roman_numeral(sp.chord, key),
         "start": round(sp.start, 2), "end": round(sp.end, 2), "bar": sp.bar}
        for sp in spans if sp.chord
    ]
    simple_spans = [
        {"start": round(sp.start, 3), "end": round(sp.end, 3), "bar": sp.bar,
         "label": harmony.simplify_chord(sp.chord, key).name(flats),
         "root": harmony.simplify_chord(sp.chord, key).root,
         "quality": harmony.simplify_chord(sp.chord, key).quality}
        for sp in spans if sp.chord
    ]

    armonia = {
        "tonalidad": key.name(),
        "alternativas": key.alternatives,
        "escala": [(FLAT if flats else SHARP)[p] for p in key.scale()],
        "pedal": pedal,
        "ambiguos": ambiguos[:40],
        "n_ambiguos": len(ambiguos),
        "capo_sugerido": capo,
        "frases": frases[:24],
        "grados": grados,
    }

    return Analysis(
        armonia=armonia,
        contraste=contraste,
        simple=simple_spans,
        chroma=[[round(float(v), 3) for v in row] for row in chroma],
        bass=[[round(float(v), 3) for v in row] for row in bass],
        source={**source.to_dict(), "sample_rate": sr, "fin_musica": fin or round(len(y) / sr, 2)},
        key={"tonic": tonic, "mode": mode, "confidence": round(conf, 2), "flats": flats},
        tempo={"bpm": rb["bpm"], "meter": rb["metro"], "beat_count": len(beats),
               "pulsos_por_compas": rb["pulsos"], "metodo": rb["metodo"]},
        beats=beats_out,
        chords=[{"start": round(s.start, 3), "end": round(s.end, 3),
                 "label": s.chord.name(flats) if s.chord else "N",
                 "root": s.chord.root if s.chord else None,
                 "quality": s.chord.quality if s.chord else None,
                 "bar": s.bar, "beat": s.beat,
                 "confidence": round(s.confidence, 3)} for s in spans],
        notes=notes_out,
        tab=tab_out,
        timings=timings,
    )
