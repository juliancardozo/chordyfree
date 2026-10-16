"""Busqueda online de letra y acordes, contrastada contra el analisis del audio.

Junta tres fuentes:

  * LaCuerda.net — transcripciones de acordes sobre letra hechas por usuarios.
  * LRCLIB       — letras abiertas, muchas sincronizadas linea por linea.
  * Cifra Club   — se intenta; hoy bloquea clientes automatizados (ver cifraclub.py).

Lo importante no es encontrar *una* transcripcion sino elegir la correcta. El
buscador de LaCuerda devuelve versiones de otros artistas, temas homonimos y
transcripciones en otro tono. Por eso cada candidata se puntua dos veces: por
parecido de titulo/artista y por cuanto concuerdan sus acordes con los que se
escucharon en el audio (`playalong.comparar`). Una transcripcion con el titulo
exacto pero acordes que no suenan en la grabacion pierde contra otra version
que si los tiene.

Todo queda en `out/<slug>/fuentes.json` y en archivos de texto al lado.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from . import cifraclub, lacuerda, letras
from .playalong import _vocabulario, comparar, parse_letra

MAX_TRANSCRIPCIONES = 4      # paginas a abrir: cada una cuesta 5 s de crawl-delay
MIN_LETRA = 0.35             # fraccion minima de la letra de la hoja que debe estar en LRCLIB


def _palabras(texto: str) -> set[str]:
    return {w for w in lacuerda._norm(texto).split() if len(w) > 3}


def similitud_letra(lineas: list[dict], letra: str) -> float | None:
    """Que fraccion de las palabras de la hoja aparece en la letra de referencia.

    None si la hoja casi no tiene letra (solo acordes): ahi no hay nada que
    comparar y no se puede descartar por este criterio.
    """
    hoja = _palabras(" ".join(l.get("txt", "") for l in lineas
                              if l["tipo"] in ("par", "letra")))
    if len(hoja) < 8:
        return None
    ref = _palabras(letra)
    return len(hoja & ref) / len(hoja)


def titulo_y_artista(a) -> tuple[str, str]:
    """El mejor par (tema, artista) que se puede deducir del analisis."""
    src = a.source or {}
    ev = ((a.contraste or {}).get("evidencia") or {})
    if src.get("track"):
        # El campo `track` tambien trae ruido: "Colombina (Remastered)".
        return lacuerda.limpiar_titulo(src["track"])[0] or src["track"], src.get("artist", "")
    titulo, art_tit = lacuerda.limpiar_titulo(src.get("title", ""))
    # En "Artista - Tema" el artista del titulo es mas confiable que el canal.
    artista = art_tit or src.get("artist", "")
    if ev.get("titulo") and ev.get("artista") and not art_tit:
        return ev["titulo"], ev["artista"]
    return titulo, artista


def otros_artistas(a, principal: str) -> list[str]:
    """Artistas secundarios del titulo: 'Jorge Drexler, Falta y Resto - ...'."""
    import re
    raw = (a.source or {}).get("title", "")
    if " - " not in raw:
        return []
    cabeza = raw.partition(" - ")[0]
    partes = re.split(r"\s*(?:,|&|\bft\.?|\bfeat\.?|\bcon\b|\bx\b)\s*", cabeza, flags=re.I)
    return [p.strip() for p in partes
            if p.strip() and lacuerda._norm(p) != lacuerda._norm(principal)][:2]


def _progreso_nulo(_msg: str) -> None:
    pass


def buscar(a, outdir: Path, cache: Path, consulta: str = "",
           artista: str = "", progress=_progreso_nulo,
           usar_lacuerda: bool = True, usar_letras: bool = True) -> dict:
    """Busca letra y acordes del tema analizado y guarda todo en `outdir`."""
    if consulta:
        titulo = consulta
    else:
        titulo, art = titulo_y_artista(a)
        artista = artista or art
    duracion = float((a.source or {}).get("fin_musica")
                     or (a.source or {}).get("duration") or 0)

    out: dict = {"consulta": {"titulo": titulo, "artista": artista},
                 "lacuerda": {"estado": "omitido", "candidatos": []},
                 "letra": {"estado": "omitido"},
                 "cifraclub": {}}

    # --- Letra (LRCLIB) ---
    if usar_letras:
        progress(f"  buscando letra en LRCLIB...")
        try:
            ls = letras.buscar(titulo, artista, duracion, cache=cache)
            mejor = letras.mejor(ls)
        except Exception as e:
            ls, mejor = [], None
            out["letra"] = {"estado": f"error: {e}"}
        if mejor:
            out["letra"] = {"estado": "ok", **mejor.to_dict(),
                            "alternativas": [{"id": l.id, "tema": l.tema,
                                              "artista": l.artista, "duracion": l.duracion,
                                              "puntaje": l.puntaje}
                                             for l in ls[1:5]]}
            (outdir / "letra.txt").write_text(mejor.texto, encoding="utf-8")
            sinc = "sincronizada" if mejor.sincronizada else "sin sincronizar"
            progress(f"    letra encontrada: {mejor.artista} - {mejor.tema} ({sinc})")
        elif usar_letras and out["letra"].get("estado") == "omitido":
            out["letra"] = {"estado": "sin resultados"}

    letra_ref = ""
    if out["letra"].get("estado") == "ok":
        letra_ref = out["letra"].get("texto") or " ".join(
            x["txt"] for x in out["letra"].get("sincronizada", []))

    # --- LaCuerda: candidatos por titulo, despues por concordancia con el audio
    if usar_lacuerda:
        progress(f"  buscando acordes en LaCuerda: {titulo!r} {artista!r}")
        try:
            cands = lacuerda.buscar(titulo, artista, cache=cache, detalle=0,
                                    otros=[] if consulta else otros_artistas(a, artista))
        except Exception as e:
            cands = []
            out["lacuerda"]["estado"] = f"error: {e}"
        tope = max((c.puntaje for c in cands), default=0)
        utiles = [c for c in cands
                  if c.puntaje >= max(2.5, tope - 1.5)][:MAX_TRANSCRIPCIONES]
        evaluados = []
        cola = list(utiles)
        leidas = 0
        while cola and leidas < MAX_TRANSCRIPCIONES + 2:
            c = cola.pop(0)
            leidas += 1
            progress(f"    leyendo {c.artista} - {c.tema} (puntaje titulo {c.puntaje})")
            try:
                if not c.versiones:
                    lacuerda.leer_pagina(c, cache)
                texto = lacuerda.leer_transcripcion(c.url, cache)
                # Un tema popular tiene varias versiones de acordes: con un
                # candidato fuerte, se suman las dos siguientes a la cola, porque
                # la primera no siempre es la que mejor concuerda con la grabacion.
                if leidas == 1 and c.puntaje >= 6 and len(c.versiones) > 1:
                    for n, u in enumerate(c.versiones[1:3], 2):
                        if u != c.url:
                            v = lacuerda.Candidato(**{**c.to_dict(), "url": u,
                                                      "versiones": [u]})
                            num = re.search(r"-(\d+)\.shtml$", u)
                            v.tema = f"{c.tema} (versión {num.group(1) if num else n})"
                            cola.insert(n - 2, v)
            except Exception as e:
                progress(f"    no se pudo leer: {e}")
                continue
            lineas = parse_letra(texto) if texto.strip() else []
            voc = _vocabulario(lineas)
            comp = comparar(voc, a) if voc else {"offset": 0, "puntaje": 0.0,
                                                  "cobertura": 0.0, "cobertura_hoja": 0.0}
            # El titulo pesa hasta ~7 puntos; la concordancia con el audio,
            # hasta 6; la letra, hasta 6. La concordancia armonica sola no
            # alcanza: con tres o cuatro acordes comunes, cualquier tema pop
            # "concuerda" en algun tono. La letra es la prueba independiente.
            sim = similitud_letra(lineas, letra_ref) if letra_ref else None
            total = c.puntaje + 6.0 * comp.get("puntaje", 0) / 100.0
            if sim is not None:
                total += 6.0 * sim
            d = c.to_dict()
            d.update({"texto": texto, "concordancia": comp, "total": round(total, 2),
                      "letra_coincide": None if sim is None else round(100 * sim),
                      "n_acordes": len(voc),
                      "lineas_con_acordes": sum(1 for l in lineas if l["tipo"] == "par")})
            if sim is not None and sim < MIN_LETRA:
                d["descartada"] = "la letra no coincide: es otra cancion con el mismo titulo"
            evaluados.append(d)
            progress(f"      concordancia con el audio {comp.get('puntaje', 0)}%"
                     f" (desplazamiento {comp.get('offset', 0):+d})"
                     + (f", letra {round(100 * sim)}%" if sim is not None else "")
                     + (" -> descartada" if d.get("descartada") else ""))

        evaluados.sort(key=lambda d: -d["total"])
        out["lacuerda"]["candidatos"] = [
            {k: v for k, v in d.items() if k != "texto"} for d in evaluados] + [
            {"artista": c.artista, "tema": c.tema, "url": c.url, "puntaje": c.puntaje,
             "no_leido": True} for c in cands[:8] if c not in utiles]
        validos = [d for d in evaluados if d["n_acordes"] and not d.get("descartada")]
        if validos:
            elegido = validos[0]
            out["lacuerda"]["estado"] = "ok"
            out["lacuerda"]["elegido"] = {k: v for k, v in elegido.items() if k != "texto"}
            (outdir / "acordes-online.txt").write_text(elegido["texto"], encoding="utf-8")
        else:
            # Una transcripcion elegida en una busqueda anterior ya no vale.
            (outdir / "acordes-online.txt").unlink(missing_ok=True)
            if out["lacuerda"]["estado"] == "omitido":
                out["lacuerda"]["estado"] = ("sin resultados" if not cands else
                                             "sin coincidencias")

    # --- Cifra Club: se intenta, y si bloquea queda el enlace para hacerlo a mano
    cc = {"busqueda": cifraclub.url_busqueda(titulo, artista),
          "probable": cifraclub.url_probable(titulo, artista)}
    if cc["probable"]:
        try:
            texto = cifraclub.leer(cc["probable"], cache)
            if texto.strip():
                cc["estado"] = "ok"
                (outdir / "acordes-cifraclub.txt").write_text(texto, encoding="utf-8")
            else:
                cc["estado"] = "no encontrada"
        except cifraclub.Bloqueado as e:
            cc["estado"] = "bloqueado"
            cc["detalle"] = str(e)
        except Exception as e:
            cc["estado"] = f"error: {e}"
    out["cifraclub"] = cc

    guardar(out, outdir)
    return out


def guardar(f: dict, outdir: Path) -> Path:
    p = outdir / "fuentes.json"
    p.write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def cargar(outdir: Path) -> dict:
    p = outdir / "fuentes.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def transcripcion_activa(outdir: Path) -> tuple[str, dict]:
    """El texto acordes-sobre-letra a usar y de donde salio.

    Prioridad: lo que el usuario importo a mano (siempre gana), despues Cifra
    Club si se pudo bajar, despues LaCuerda.
    """
    f = cargar(outdir)
    for nombre, origen in (("acordes-importados.txt", "importada"),
                           ("acordes-cifraclub.txt", "Cifra Club"),
                           ("acordes-online.txt", "LaCuerda"),
                           ("ejemplo-internet.txt", "archivo local")):
        p = outdir / nombre
        if p.exists() and p.read_text(encoding="utf-8", errors="replace").strip():
            meta = {"origen": origen, "archivo": nombre}
            if origen == "LaCuerda":
                meta.update((f.get("lacuerda") or {}).get("elegido") or {})
            if origen == "importada":
                mp = outdir / "acordes-importados.json"
                if mp.exists():
                    try:
                        meta.update(json.loads(mp.read_text(encoding="utf-8")))
                    except Exception:
                        pass
            return p.read_text(encoding="utf-8", errors="replace"), meta
    return "", {}


def importar(outdir: Path, texto: str, url: str = "", origen: str = "") -> Path:
    """Guarda una transcripcion traida a mano (pegada o desde el marcador)."""
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    # Cifra Club agrega lineas de tablatura y avisos al pie: se conservan, el
    # parser las trata como texto. Solo se recortan espacios sobrantes.
    texto = re.sub(r"\n{4,}", "\n\n\n", texto).strip("\n")
    p = outdir / "acordes-importados.txt"
    p.write_text(texto, encoding="utf-8")
    if not origen:
        origen = ("Cifra Club" if "cifraclub" in url else
                  "LaCuerda" if "lacuerda" in url else "importada")
    (outdir / "acordes-importados.json").write_text(
        json.dumps({"url": url, "sitio": origen}, ensure_ascii=False), encoding="utf-8")
    return p
