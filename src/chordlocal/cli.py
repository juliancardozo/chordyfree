"""CLI de ChordLocal.

  chordlocal analizar <url-o-archivo> [--transponer N] [--capo N] [--no-notes]
  chordlocal mostrar  <analysis.json> [--transponer N] [--capo N]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
OUT = ROOT / "out"


def _emit(a, outdir: Path, transpose: int, capo: int, want_notes: bool) -> None:
    """Escribe todas las salidas y muestra el resumen por consola."""
    from . import render

    outdir.mkdir(parents=True, exist_ok=True)

    print()
    print(render.summary(a, transpose, capo))
    print()
    print("  ACORDES POR COMPAS")
    print("  " + "-" * 50)
    grid = render.chord_grid(a, transpose, capo)
    print("\n".join("  " + l for l in grid.splitlines()[:24]))

    shapes = render.chord_shapes(a, transpose, capo)
    if shapes:
        print("  DIGITACIONES" + (f"  (con cejilla en {capo})" if capo else ""))
        print("  " + "-" * 50)
        print("\n".join("  " + l for l in shapes.splitlines()))

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

    try:
        written.append(render.musicxml(a, outdir / "partitura.musicxml",
                                       transpose, include_notes=want_notes))
    except Exception as e:
        print(f"  ! no se pudo generar MusicXML: {e}")

    try:
        written.append(render.midi(a, outdir / "cancion.mid", transpose))
    except Exception as e:
        print(f"  ! no se pudo generar MIDI: {e}")

    try:
        from . import sheet
        written.append(sheet.build(a, outdir / "hoja.html"))
    except Exception as e:
        print(f"  ! no se pudo generar la hoja HTML: {e}")

    arm = a.armonia or {}
    if arm:
        print("  ANALISIS ARMONICO")
        print("  " + "-" * 50)
        print(f"  Tonalidad : {arm.get('tonalidad')}   escala: {' '.join(arm.get('escala', []))}")
        if arm.get("capo_sugerido"):
            cs = arm["capo_sugerido"]
            print(f"  Cejilla   : traste {cs['capo']} -> formas de {cs['forma']}")
        if arm.get("pedal"):
            pd = arm["pedal"]
            print(f"  Pedal     : {pd['nota']} en el bajo el {pd['fraccion']:.0%} del tema")
        if arm.get("n_ambiguos"):
            print(f"  Sin 3a    : {arm['n_ambiguos']} acordes con la tercera indefinida")
        print()

    con = a.contraste or {}
    if con.get("evidencia"):
        ev = con["evidencia"]
        print("  CONTRASTE CON INTERNET")
        print("  " + "-" * 50)
        print(f"  Fuentes   : {', '.join(ev.get('fuentes') or ['ninguna'])}")
        t, k = con.get("tempo", {}), con.get("tonalidad", {})
        print(f"  Tempo     : {t.get('detectado')} propio vs {t.get('externo')} externo -> {t.get('veredicto')}")
        print(f"  Tonalidad : {k.get('detectada')} propia vs {k.get('externa')} externa -> {k.get('veredicto')}")
        print()

    print("  ARCHIVOS GENERADOS")
    print("  " + "-" * 50)
    for p in written:
        try:
            rel = p.relative_to(Path.cwd())
        except ValueError:
            rel = p
        print(f"  {rel}  ({p.stat().st_size / 1024:.1f} KB)")
    print()
    print("  Abri hoja.html en el navegador: transposicion y cejilla en vivo.")
    print("  Abri partitura.musicxml en MuseScore para pentagrama + tablatura.")
    print()


def cmd_analizar(args) -> int:
    from .analyze import analyze
    from .fetch import resolve

    print(f"\nChordLocal {__version__}")
    print(f"Fuente: {args.target}\n")

    try:
        src = resolve(args.target, CACHE)
    except Exception as e:
        print(f"error obteniendo el audio: {e}", file=sys.stderr)
        return 1

    print(f"  audio: {src.wav.name}  ({src.duration:.0f}s)  {src.title}")

    try:
        a = analyze(src, with_notes=not args.no_notes,
                    with_web=not args.sin_internet)
    except Exception as e:
        print(f"error analizando: {e}", file=sys.stderr)
        return 1

    slug = src.video_id or "track"
    _emit(a, OUT / slug, args.transponer, args.capo, not args.no_notes)
    return 0


def cmd_mostrar(args) -> int:
    from .analyze import Analysis
    a = Analysis.load(Path(args.analysis))
    _emit(a, Path(args.analysis).parent, args.transponer, args.capo, bool(a.tab))
    return 0


def cmd_app(args) -> int:
    from .app import run
    run(host=args.host, port=args.port, abrir=not args.sin_abrir)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="chordlocal",
        description="Analiza canciones y genera acordes, tablaturas y partituras.")
    p.add_argument("--version", action="version", version=f"chordlocal {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analizar", help="analiza una URL de YouTube o un archivo local")
    a.add_argument("target", help="URL o ruta de audio")
    a.add_argument("--transponer", type=int, default=0, metavar="N",
                   help="transponer N semitonos")
    a.add_argument("--capo", type=int, default=0, metavar="N",
                   help="digitar como si hubiera cejilla en el traste N")
    a.add_argument("--no-notes", action="store_true",
                   help="omitir transcripcion de notas (mas rapido, sin tablatura)")
    a.add_argument("--sin-internet", action="store_true",
                   help="no contrastar con MusicBrainz / AcousticBrainz")
    a.set_defaults(func=cmd_analizar)

    m = sub.add_parser("mostrar", help="re-renderiza un analysis.json ya calculado")
    m.add_argument("analysis", help="ruta a analysis.json")
    m.add_argument("--transponer", type=int, default=0, metavar="N")
    m.add_argument("--capo", type=int, default=0, metavar="N")
    m.set_defaults(func=cmd_mostrar)

    g = sub.add_parser("app", help="abre la interfaz grafica en el navegador")
    g.add_argument("--port", type=int, default=7700)
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--sin-abrir", action="store_true",
                   help="no abrir el navegador automaticamente")
    g.set_defaults(func=cmd_app)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
