"""CLI de ChordLocal.

  chordlocal analizar <url-o-archivo> [--transponer N] [--capo N] [--no-notes] [--sin-buscar]
  chordlocal mostrar  <analysis.json> [--transponer N] [--capo N]
  chordlocal buscar   <analysis.json> [--tema "..."] [--artista "..."]
  chordlocal tocar    <analysis.json> [--letra archivo.txt]
  chordlocal app      [--port 7700]
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
    from . import render, salidas

    print()
    print(render.summary(a, transpose, capo))
    print()
    print("  ACORDES POR COMPAS")
    print("  " + "-" * 50)
    grid = render.chord_grid(a, transpose, capo)
    print("\n".join("  " + l for l in grid.splitlines()[:24]))

    written = salidas.escribir(a, outdir, transpose, capo, want_notes)

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
    print(f"  Abri {outdir / 'cancion.html'}: letra, acordes, audio y analisis.")
    print()


def _buscar_online(a, outdir: Path, tema: str = "", artista: str = "") -> dict:
    from . import fuentes
    outdir.mkdir(parents=True, exist_ok=True)
    print("\n  BUSQUEDA ONLINE (letra y acordes)")
    print("  " + "-" * 50)
    f = fuentes.buscar(a, outdir, CACHE, consulta=tema, artista=artista,
                       progress=lambda m: print(m, flush=True))
    lc, le, cc = f["lacuerda"], f["letra"], f["cifraclub"]
    el = lc.get("elegido")
    if el:
        con = el.get("concordancia", {})
        print(f"  Acordes  : LaCuerda, {el['artista']} - {el['tema']}"
              f" (concordancia con el audio {con.get('puntaje', 0)}%"
              f", desplazamiento {con.get('offset', 0):+d})")
    else:
        print(f"  Acordes  : LaCuerda {lc.get('estado')}")
    print(f"  Letra    : {le.get('estado')}"
          + (f" ({le.get('artista')} - {le.get('tema')}, "
             f"{'sincronizada' if le.get('sincronizada') else 'sin sincronizar'})"
             if le.get("estado") == "ok" else ""))
    print(f"  CifraClub: {cc.get('estado', '-')}"
          + (f" -> abrila a mano: {cc.get('probable')}" if cc.get("estado") == "bloqueado" else ""))
    return f


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

    outdir = OUT / (src.video_id or "track")
    if not args.sin_buscar and not args.sin_internet:
        try:
            _buscar_online(a, outdir)
        except Exception as e:
            print(f"  ! la busqueda online fallo: {e}")
    _emit(a, outdir, args.transponer, args.capo, not args.no_notes)
    return 0


def cmd_mostrar(args) -> int:
    from .analyze import Analysis
    a = Analysis.load(Path(args.analysis))
    _emit(a, Path(args.analysis).parent, args.transponer, args.capo, bool(a.tab))
    return 0


def cmd_buscar(args) -> int:
    """Busca letra y acordes online y regenera cancion.html."""
    from .analyze import Analysis
    from . import pagina

    ruta = Path(args.analysis)
    a = Analysis.load(ruta)
    try:
        _buscar_online(a, ruta.parent, args.tema, args.artista)
    except Exception as e:
        print(f"error en la busqueda: {e}", file=sys.stderr)
        return 1
    p = pagina.build(a, ruta.parent)
    print(f"\n  {p}\n")
    return 0


def cmd_tocar(args) -> int:
    """Regenera cancion.html; con --letra importa antes una transcripcion local."""
    from .analyze import Analysis
    from . import fuentes, pagina

    ruta = Path(args.analysis)
    a = Analysis.load(ruta)
    if args.letra:
        texto = Path(args.letra).read_text(encoding="utf-8", errors="replace")
        fuentes.importar(ruta.parent, texto, origen="archivo local")
    d = pagina.armar(a, ruta.parent)
    p = pagina.build(a, ruta.parent)
    print(f"\n  {p}  ({p.stat().st_size / 1024:.1f} KB)")
    print(f"  modo: {d['modo']}   tonalidad: {d['tonalidad']}"
          + (f"   concordancia hoja/audio: {d['comparacion']['puntaje']}%"
             if d.get("comparacion") else ""))
    print()
    return 0


def cmd_app(args) -> int:
    from .app import run
    run(host=args.host, port=args.port, abrir=not args.sin_abrir)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="chordlocal",
        description="Analiza canciones y genera acordes, letra, tablaturas y partituras.")
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
                   help="no consultar nada online (ni MusicBrainz ni letras/acordes)")
    a.add_argument("--sin-buscar", action="store_true",
                   help="no buscar letra ni acordes online")
    a.set_defaults(func=cmd_analizar)

    m = sub.add_parser("mostrar", help="re-renderiza un analysis.json ya calculado")
    m.add_argument("analysis", help="ruta a analysis.json")
    m.add_argument("--transponer", type=int, default=0, metavar="N")
    m.add_argument("--capo", type=int, default=0, metavar="N")
    m.set_defaults(func=cmd_mostrar)

    b = sub.add_parser("buscar", help="busca letra y acordes online (LaCuerda, LRCLIB, Cifra Club)")
    b.add_argument("analysis", help="ruta a analysis.json")
    b.add_argument("--tema", default="", help="nombre del tema, si el del video no sirve")
    b.add_argument("--artista", default="", help="artista, si el del video no sirve")
    b.set_defaults(func=cmd_buscar)

    t = sub.add_parser("tocar", help="regenera cancion.html (letra + acordes + analisis)")
    t.add_argument("analysis", help="ruta a analysis.json")
    t.add_argument("--letra", default="", metavar="ARCHIVO",
                   help="importar este txt de acordes-sobre-letra antes de generar")
    t.set_defaults(func=cmd_tocar)

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
