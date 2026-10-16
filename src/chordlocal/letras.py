"""Letras desde LRCLIB (lrclib.net): una base abierta, con API publica y gratuita.

Lo valioso de LRCLIB no es solo la letra sino la letra *sincronizada*: cada
linea trae la marca de tiempo en que se canta. Con eso, los acordes que el
analisis detecto en el audio se pueden ubicar sobre la linea que suena en ese
momento, sin necesitar ninguna transcripcion de acordes de terceros.

La API pide que los clientes se identifiquen con un User-Agent propio; no tiene
limite de tasa publicado, pero a veces responde 503 cuando esta cargada, asi que
se reintenta con espera. Todo se cachea en disco.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .lacuerda import _norm, _tokens

API = "https://lrclib.net/api"
UA = "ChordLocal/0.2 (analisis musical personal; https://github.com/)"
TIMEOUT = 20


@dataclass
class Letra:
    """Una letra encontrada, con o sin sincronizacion."""
    id: int = 0
    tema: str = ""
    artista: str = ""
    album: str = ""
    duracion: float = 0.0
    texto: str = ""                                 # letra plana
    sincronizada: list[dict] = field(default_factory=list)   # [{t, txt}]
    instrumental: bool = False
    puntaje: float = 0.0
    motivos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _get(url: str, cache: Path | None, intentos: int = 3):
    if cache is not None:
        key = re.sub(r"[^\w.-]", "_", url.split("://", 1)[-1])[:150]
        cached = cache / f"lrclib-{key}.json"
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))

    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    ultimo: Exception | None = None
    for i in range(intentos):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as e:
            ultimo = e
            if e.code == 404:
                data = None
                break
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(1.5 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            ultimo = e
            time.sleep(1.5 * (i + 1))
    else:
        raise RuntimeError(f"LRCLIB no respondio: {ultimo}")

    if cache is not None and data is not None:
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


_LRC_RE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$")


def parse_lrc(lrc: str) -> list[dict]:
    """'[01:23.45] texto' -> [{t: 83.45, txt: 'texto'}], en orden."""
    out = []
    for linea in (lrc or "").splitlines():
        m = _LRC_RE.match(linea.strip())
        if m:
            out.append({"t": round(int(m.group(1)) * 60 + float(m.group(2)), 2),
                        "txt": m.group(3).strip()})
    out.sort(key=lambda x: x["t"])
    return out


def _a_letra(d: dict) -> Letra:
    return Letra(
        id=d.get("id") or 0,
        tema=d.get("trackName") or d.get("name") or "",
        artista=d.get("artistName") or "",
        album=d.get("albumName") or "",
        duracion=float(d.get("duration") or 0),
        texto=(d.get("plainLyrics") or "").strip(),
        sincronizada=parse_lrc(d.get("syncedLyrics") or ""),
        instrumental=bool(d.get("instrumental")),
    )


def puntuar(l: Letra, titulo: str, artista: str, duracion: float) -> Letra:
    """Que tan probable es que esta letra sea la de la grabacion analizada.

    La duracion es el criterio mas fuerte cuando hay sincronizacion: una letra
    sincronizada para otra version (en vivo, remaster con intro mas larga) trae
    las marcas de tiempo corridas, y entonces los acordes caerian sobre la
    linea equivocada.
    """
    l.motivos = []
    p = 0.0
    tt, ta = _tokens(titulo), _tokens(artista)
    lt, la = _tokens(l.tema), _tokens(l.artista)
    if tt:
        cob = len(tt & lt) / len(tt)
        p += 3.0 * cob
        from .lacuerda import limpiar_titulo
        if _norm(limpiar_titulo(l.tema)[0] or l.tema) == _norm(titulo):
            p += 1.5
            l.motivos.append("titulo exacto")
        elif cob:
            l.motivos.append(f"titulo {len(tt & lt)}/{len(tt)}")
    if ta and la and (ta & la):
        p += 2.0
        l.motivos.append("artista coincide")
    if duracion and l.duracion:
        dif = abs(duracion - l.duracion)
        if dif <= 3:
            p += 2.0
            l.motivos.append("duracion exacta")
        elif dif <= 10:
            p += 1.0
            l.motivos.append(f"duracion ±{dif:.0f}s")
        elif dif > 30:
            p -= 1.0
    if l.sincronizada:
        # Con la misma duracion, la sincronizada es estrictamente mas util.
        p += 1.5 if duracion and l.duracion and abs(duracion - l.duracion) <= 10 else 0.5
        l.motivos.append("sincronizada")
    if l.instrumental or not (l.texto or l.sincronizada):
        p -= 3.0
    l.puntaje = round(p, 2)
    return l


def buscar(titulo: str, artista: str = "", duracion: float = 0.0,
           cache: Path | None = None) -> list[Letra]:
    """Busca la letra y devuelve candidatas ordenadas de mejor a peor."""
    vistos: dict[int, Letra] = {}

    consultas = []
    if artista:
        consultas.append({"track_name": titulo, "artist_name": artista})
    consultas.append({"q": f"{artista} {titulo}".strip()})
    consultas.append({"q": titulo})

    for params in consultas:
        url = f"{API}/search?" + urllib.parse.urlencode(params)
        try:
            data = _get(url, cache) or []
        except Exception:
            continue
        for d in data:
            l = _a_letra(d)
            if l.id and l.id not in vistos:
                vistos[l.id] = puntuar(l, titulo, artista, duracion)
        # Con un candidato fuerte ya no hace falta aflojar la consulta.
        if any(l.puntaje >= 6 for l in vistos.values()):
            break

    return sorted(vistos.values(), key=lambda l: -l.puntaje)


def mejor(cands: list[Letra], minimo: float = 3.0) -> Letra | None:
    """La mejor candidata si supera un umbral razonable de coincidencia."""
    return cands[0] if cands and cands[0].puntaje >= minimo else None
