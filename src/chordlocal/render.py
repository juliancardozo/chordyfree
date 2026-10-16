"""Salidas legibles: grilla de acordes, ChordPro, tablatura ASCII y MusicXML.

MusicXML es el formato que abre MuseScore y que renderiza alphaTab en el browser,
asi que es el puente hacia una partitura/tablatura con tipografia real.
"""
from __future__ import annotations

from pathlib import Path

from .analyze import Analysis
from .theory import Chord, diagram, fingering

STRING_NAMES = ["e", "B", "G", "D", "A", "E"]  # 1a arriba, convencion de tablatura


# ----------------------------------------------------------------------------
# Grilla de acordes por compas (la vista principal de Chordify)
# ----------------------------------------------------------------------------

def chord_grid(a: Analysis, transpose: int = 0, capo: int = 0,
               per_line: int = 4) -> str:
    """Acordes agrupados por compas, N compases por linea."""
    flats = a.flats
    bars: dict[int, list[str]] = {}
    for c in a.chords:
        if c["root"] is None:
            name = "—"
        else:
            ch = Chord(root=c["root"], quality=c["quality"]).transpose(transpose)
            name = ch.name(flats)
        bars.setdefault(c["bar"], [])
        if not bars[c["bar"]] or bars[c["bar"]][-1] != name:
            bars[c["bar"]].append(name)

    if not bars:
        return "(no se detectaron acordes)\n"

    out: list[str] = []
    numbers = sorted(bars)
    for i in range(0, len(numbers), per_line):
        chunk = numbers[i:i + per_line]
        cells = [" ".join(bars[b]) for b in chunk]
        width = max(14, max(len(c) for c in cells) + 2)
        out.append("  ".join(f"c.{b:<3}".ljust(width) for b in chunk).rstrip())
        out.append("  ".join(f"| {c}".ljust(width) for c in cells).rstrip())
        out.append("")
    return "\n".join(out)


def chord_shapes(a: Analysis, transpose: int = 0, capo: int = 0) -> str:
    """Diagramas de los acordes usados, en orden de aparicion."""
    flats = a.flats
    seen: list[Chord] = []
    for c in a.chords:
        if c["root"] is None:
            continue
        ch = Chord(root=c["root"], quality=c["quality"]).transpose(transpose)
        if not any(x.root == ch.root and x.quality == ch.quality for x in seen):
            seen.append(ch)

    if not seen:
        return ""

    blocks: list[list[str]] = []
    for ch in seen:
        f = fingering(ch, capo=capo, flats=flats)
        blocks.append(diagram(f) if f else [f"  ?????   {ch.name(flats)}", "  (sin digitacion)"])

    # Imprimir de 6 en 6, lado a lado.
    lines: list[str] = []
    for i in range(0, len(blocks), 6):
        row = blocks[i:i + 6]
        height = max(len(b) for b in row)
        for b in row:
            b += [""] * (height - len(b))
        for r in range(height):
            lines.append("   ".join(b[r].ljust(12) for b in row).rstrip())
        lines.append("")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# ChordPro
# ----------------------------------------------------------------------------

def chordpro(a: Analysis, transpose: int = 0, capo: int = 0) -> str:
    """Formato ChordPro, que abre cualquier app de canciones."""
    flats = a.flats
    src = a.source
    head = [
        f"{{title: {src.get('title', 'desconocido')}}}",
        f"{{artist: {src.get('artist', '')}}}",
        f"{{key: {a.key['tonic']} {a.key['mode']}}}",
        f"{{tempo: {a.tempo['bpm']}}}",
        f"{{time: {a.tempo['meter']}}}",
    ]
    if capo:
        head.append(f"{{capo: {capo}}}")
    head.append("")

    bars: dict[int, list[str]] = {}
    for c in a.chords:
        if c["root"] is None:
            continue
        ch = Chord(root=c["root"], quality=c["quality"]).transpose(transpose)
        bars.setdefault(c["bar"], [])
        name = ch.name(flats)
        if not bars[c["bar"]] or bars[c["bar"]][-1] != name:
            bars[c["bar"]].append(name)

    body: list[str] = []
    numbers = sorted(bars)
    for i in range(0, len(numbers), 4):
        line = ""
        for b in numbers[i:i + 4]:
            line += "| " + " ".join(f"[{n}]" for n in bars[b]) + " "
        body.append(line.rstrip() + " |")
    return "\n".join(head + body) + "\n"


# ----------------------------------------------------------------------------
# Tablatura ASCII
# ----------------------------------------------------------------------------

def ascii_tab(a: Analysis, subdivisions: int = 4, bars_per_line: int = 2) -> str:
    """Tablatura por compas. `subdivisions` = columnas por beat (4 = semicorcheas)."""
    if not a.tab:
        return "(sin transcripcion de notas; correr sin --no-notes)\n"

    beats = [b["t"] for b in a.beats]
    if len(beats) < 2:
        return "(grilla de beats insuficiente)\n"

    meter = a.tempo.get("meter", "4/4")
    beats_per_bar = int(meter.split("/")[0]) if "/" in meter else 4

    # Posicion de cada nota en columnas absolutas de la grilla.
    def column(t: float) -> int:
        # Indice del beat anterior + fraccion dentro de ese beat.
        import bisect
        i = bisect.bisect_right(beats, t) - 1
        i = max(0, min(i, len(beats) - 2))
        span = beats[i + 1] - beats[i]
        frac = (t - beats[i]) / span if span > 0 else 0.0
        return i * subdivisions + int(round(frac * subdivisions))

    total_cols = (len(beats) - 1) * subdivisions
    grid: list[dict[int, str]] = [dict() for _ in range(total_cols + 1)]
    for t in a.tab:
        col = min(column(t["start"]), total_cols)
        grid[col][t["string"]] = str(t["fret"])

    cols_per_bar = beats_per_bar * subdivisions
    n_bars = (total_cols + cols_per_bar - 1) // cols_per_bar

    out: list[str] = []
    for start_bar in range(0, n_bars, bars_per_line):
        group = range(start_bar, min(start_bar + bars_per_line, n_bars))
        rows = {s: "" for s in range(6)}
        header = ""
        for b in group:
            c0 = b * cols_per_bar
            header += f"c.{b + 1}".ljust(cols_per_bar * 3 + 1)
            for s in range(6):
                cell = ""
                for c in range(c0, c0 + cols_per_bar):
                    v = grid[c].get(s, "") if c < len(grid) else ""
                    cell += (v.rjust(2, "-") if v else "--") + "-"
                rows[s] += "|" + cell
        out.append(header.rstrip())
        for s in range(6):
            # string 5 = 1a cuerda (aguda) va arriba
            idx = 5 - s
            out.append(f"{STRING_NAMES[s]}{rows[idx]}|")
        out.append("")
    return "\n".join(out)


# ----------------------------------------------------------------------------
# MusicXML (partitura + tablatura con tipografia real)
# ----------------------------------------------------------------------------

def musicxml(a: Analysis, path: Path, transpose: int = 0,
             include_notes: bool = True) -> Path:
    """Escribe MusicXML: cifrado americano + notas transcriptas.

    Se abre en MuseScore (gratis) y alphaTab lo renderiza en el browser con
    pentagrama y tablatura simultaneos.
    """
    from music21 import chord as m21chord
    from music21 import harmony, instrument, metadata, meter as m21meter
    from music21 import note as m21note
    from music21 import stream, tempo as m21tempo

    beats = [b["t"] for b in a.beats]
    if len(beats) < 2:
        raise RuntimeError("grilla de beats insuficiente para generar partitura")

    def to_offset(t: float) -> float:
        """Segundos -> offset en negras, usando la grilla de beats real.

        Esto es lo que mantiene la partitura alineada aunque el tempo fluctue.
        """
        import bisect
        i = bisect.bisect_right(beats, t) - 1
        i = max(0, min(i, len(beats) - 2))
        span = beats[i + 1] - beats[i]
        frac = (t - beats[i]) / span if span > 0 else 0.0
        return i + frac

    def quantize(x: float, grid: float = 0.25) -> float:
        """Cuantiza a la rejilla y nunca devuelve negativo.

        Una nota detectada un instante antes del primer beat daria offset
        negativo, y music21 no puede ubicarla en ningun compas.
        """
        return max(0.0, round(x / grid) * grid)

    score = stream.Score()
    score.metadata = metadata.Metadata(
        title=a.source.get("title", "desconocido"),
        composer=a.source.get("artist", ""),
    )

    part = stream.Part()
    part.insert(0, instrument.AcousticGuitar())
    part.insert(0, m21meter.TimeSignature(a.tempo.get("meter", "4/4")))
    if a.tempo.get("bpm"):
        part.insert(0, m21tempo.MetronomeMark(number=a.tempo["bpm"]))

    # Cifrado: un simbolo por cambio de acorde.
    for c in a.chords:
        if c["root"] is None:
            continue
        ch = Chord(root=c["root"], quality=c["quality"]).transpose(transpose)
        try:
            cs = harmony.ChordSymbol(ch.name(a.flats))
            cs.quarterLength = max(0.25, quantize(to_offset(c["end"]) - to_offset(c["start"])))
            part.insert(quantize(to_offset(c["start"])), cs)
        except Exception:
            continue  # simbolo que music21 no sabe parsear: mejor omitirlo

    if include_notes and a.notes:
        # Agrupar notas simultaneas en acordes para que la partitura no quede
        # con voces superpuestas ilegibles.
        groups: dict[float, list[int]] = {}
        for n in a.notes:
            off = quantize(to_offset(n["start"]))
            dur = max(0.25, quantize(to_offset(n["end"]) - to_offset(n["start"])))
            groups.setdefault(off, []).append(n["midi"] + transpose)

        melody = stream.Voice()
        for off in sorted(groups):
            midis = sorted(set(groups[off]))[:6]
            if len(midis) == 1:
                el = m21note.Note(midis[0])
            else:
                el = m21chord.Chord(midis)
            el.quarterLength = 0.5
            melody.insert(off, el)
        part.insert(0, melody)

    score.insert(0, part)
    path.parent.mkdir(parents=True, exist_ok=True)
    score.write("musicxml", fp=str(path))
    return path


def midi(a: Analysis, path: Path, transpose: int = 0) -> Path:
    """MIDI con dos pistas: acordes y notas transcriptas (feature premium #10)."""
    import pretty_midi
    from .theory import QUALITIES

    pm = pretty_midi.PrettyMIDI(initial_tempo=a.tempo.get("bpm") or 120.0)

    chords_inst = pretty_midi.Instrument(program=24, name="acordes")  # guitarra nylon
    for c in a.chords:
        if c["root"] is None:
            continue
        root = (c["root"] + transpose) % 12
        for iv in QUALITIES.get(c["quality"] or "maj", (0, 4, 7)):
            pitch = 48 + ((root + iv) % 12) + 12 * ((root + iv) // 12)
            chords_inst.notes.append(pretty_midi.Note(
                velocity=70, pitch=int(pitch), start=c["start"], end=c["end"]))
    pm.instruments.append(chords_inst)

    if a.notes:
        lead = pretty_midi.Instrument(program=29, name="notas")  # guitarra con drive
        for n in a.notes:
            lead.notes.append(pretty_midi.Note(
                velocity=80, pitch=int(n["midi"] + transpose),
                start=n["start"], end=n["end"]))
        pm.instruments.append(lead)

    path.parent.mkdir(parents=True, exist_ok=True)
    pm.write(str(path))
    return path


def summary(a: Analysis, transpose: int = 0, capo: int = 0) -> str:
    """Encabezado con lo esencial de la cancion."""
    src = a.source
    k = a.key
    tonic = Chord(root=0, quality="maj")  # placeholder para transponer la tonalidad
    from .theory import PITCH, SHARP, FLAT
    ti = PITCH.get(k["tonic"], 0)
    names = FLAT if a.flats else SHARP
    key_name = f"{names[(ti + transpose) % 12]} {k['mode']}"

    lines = [
        f"  {src.get('title', 'desconocido')}",
    ]
    if src.get("artist"):
        lines.append(f"  {src['artist']}")
    lines += [
        "",
        f"  Tonalidad : {key_name}  (confianza {k.get('confidence', 0):.0%})",
        f"  Tempo     : {a.tempo.get('bpm')} BPM, compas {a.tempo.get('meter')}",
        f"  Duracion  : {src.get('duration', 0):.0f} s",
        f"  Acordes   : {len([c for c in a.chords if c['root'] is not None])} cambios",
    ]
    if a.notes:
        lines.append(f"  Notas     : {len(a.notes)} transcriptas")
    if transpose:
        lines.append(f"  Transpuesto: {transpose:+d} semitonos")
    if capo:
        lines.append(f"  Cejilla   : traste {capo}")
    if a.timings:
        total = sum(a.timings.values())
        detail = ", ".join(f"{k}={v}s" for k, v in a.timings.items())
        lines.append(f"  Analisis  : {total:.0f}s ({detail})")
    return "\n".join(lines)
