"""Escritura de todas las salidas de una cancion. La usan la CLI y la app."""
from __future__ import annotations

from pathlib import Path


def escribir(a, outdir: Path, transpose: int = 0, capo: int = 0,
             want_notes: bool = True, aviso=print) -> list[Path]:
    """Escribe analysis.json y los derivados. Devuelve las rutas escritas."""
    from . import pagina, render, sheet

    outdir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    p = outdir / "analysis.json"
    p.write_text(a.to_json())
    written.append(p)

    p = outdir / "acordes.txt"
    p.write_text(render.summary(a, transpose, capo) + "\n\n"
                 + render.chord_grid(a, transpose, capo) + "\n"
                 + render.chord_shapes(a, transpose, capo))
    written.append(p)

    p = outdir / "cancion.chordpro"
    p.write_text(render.chordpro(a, transpose, capo))
    written.append(p)

    if want_notes and a.tab:
        p = outdir / "tablatura.txt"
        p.write_text(render.ascii_tab(a))
        written.append(p)

    for nombre, fn in (
        ("MusicXML", lambda: render.musicxml(a, outdir / "partitura.musicxml",
                                             transpose, include_notes=want_notes)),
        ("MIDI", lambda: render.midi(a, outdir / "cancion.mid", transpose)),
        ("hoja analitica", lambda: sheet.build(a, outdir / "hoja.html")),
        ("cancion.html", lambda: pagina.build(a, outdir)),
    ):
        try:
            written.append(fn())
        except Exception as e:
            aviso(f"  ! no se pudo generar {nombre}: {e}")
    return written
