"""Contraste del analisis contra fuentes abiertas de internet.

Se usan MusicBrainz (metadata canonica) y AcousticBrainz (tonalidad y BPM
analizados por la comunidad). Las transcripciones de acordes y las letras se buscan aparte, en
`fuentes.py`; aca solo se contrastan tempo y tonalidad, que es donde los datos
abiertos existen y donde el detector mas se equivoca.

Todo es best-effort: si no hay red o la cancion no esta en la base, el analisis
sigue igual y simplemente no hay contraste.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

UA = "ChordLocal/0.1 (analisis musical personal)"
TIMEOUT = 12


@dataclass
class Evidence:
    """Lo que las fuentes externas dicen de la cancion."""
    mbid: str = ""
    titulo: str = ""
    artista: str = ""
    duracion: float = 0.0
    bpm: float | None = None
    tonica: str | None = None
    modo: str | None = None
    fuentes: list[str] = field(default_factory=list)
    notas: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _get(url: str) -> dict | None:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def _clean_title(raw: str) -> tuple[str, str]:
    """Separa 'Artista - "Tema"' y limpia ruido tipico de titulos de YouTube."""
    t = raw.strip()
    t = re.sub(r"\((?:official|video|audio|lyric|remaster(?:ed)?|hd|4k)[^)]*\)",
               "", t, flags=re.I)
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"\s*\|.*$", "", t)
    artista = ""
    if " - " in t:
        artista, _, t = t.partition(" - ")
    t = t.strip(' "\'“”')
    artista = re.sub(r"\s*ft\.?\s.*$", "", artista, flags=re.I).strip()
    return t.strip(), artista


def lookup_musicbrainz(titulo: str, artista: str) -> dict | None:
    """Busca la grabacion canonica. Devuelve el mejor match o None."""
    q = f'recording:"{titulo}"'
    if artista:
        q += f' AND artist:"{artista}"'
    url = ("https://musicbrainz.org/ws/2/recording?query="
           + urllib.parse.quote(q) + "&fmt=json&limit=5")
    data = _get(url)
    if not data or not data.get("recordings"):
        return None
    best = data["recordings"][0]
    if best.get("score", 0) < 70:
        return None
    return best


def lookup_acousticbrainz(mbid: str) -> dict | None:
    """Tonalidad y BPM analizados, si la grabacion fue enviada a AcousticBrainz."""
    return _get(f"https://acousticbrainz.org/api/v1/{mbid}/low-level")


def gather(titulo_raw: str, artista_raw: str = "") -> Evidence:
    """Reune toda la evidencia externa disponible sobre la cancion."""
    titulo, art_from_title = _clean_title(titulo_raw)
    artista = artista_raw or art_from_title
    ev = Evidence(titulo=titulo, artista=artista)

    rec = lookup_musicbrainz(titulo, artista)
    if not rec:
        ev.notas.append("sin coincidencia en MusicBrainz")
        return ev

    ev.mbid = rec.get("id", "")
    ev.titulo = rec.get("title", titulo)
    credits = rec.get("artist-credit") or []
    if credits:
        ev.artista = credits[0].get("name", artista)
    if rec.get("length"):
        ev.duracion = rec["length"] / 1000.0
    ev.fuentes.append("MusicBrainz")

    ab = lookup_acousticbrainz(ev.mbid) if ev.mbid else None
    if ab:
        tonal = ab.get("tonal") or {}
        rhythm = ab.get("rhythm") or {}
        if tonal.get("key_key"):
            ev.tonica = tonal["key_key"]
            ev.modo = tonal.get("key_scale")
        if rhythm.get("bpm"):
            ev.bpm = round(float(rhythm["bpm"]), 1)
        ev.fuentes.append("AcousticBrainz")
    else:
        ev.notas.append("sin datos en AcousticBrainz")

    return ev


def reconcile_tempo(detectado: float, externo: float | None) -> dict:
    """Compara el tempo propio con el externo, contemplando medio y doble tiempo.

    Los detectores de beats confunden con frecuencia el nivel metrico: marcan
    corcheas en vez de negras y reportan el doble del tempo real. Si la fuente
    externa coincide con la mitad o el doble, el desacuerdo es de nivel metrico,
    no de deteccion, y se puede corregir con confianza.
    """
    out = {"detectado": detectado, "externo": externo, "veredicto": "sin contraste",
           "sugerido": detectado}
    if not externo or not detectado:
        return out
    for factor, nombre in ((1.0, "coincide"), (0.5, "mitad de tiempo"),
                           (2.0, "doble de tiempo")):
        if abs(detectado * factor - externo) <= max(2.5, externo * 0.04):
            out["veredicto"] = nombre
            out["sugerido"] = round(detectado * factor, 1)
            return out
    out["veredicto"] = "discrepan"
    return out


def reconcile_key(tonica_det: str, modo_det: str,
                  tonica_ext: str | None, modo_ext: str | None) -> dict:
    """Compara la tonalidad propia con la externa, contemplando la relativa."""
    from .theory import PITCH, SHARP

    out = {"detectada": f"{tonica_det} {modo_det}", "externa": None,
           "veredicto": "sin contraste"}
    if not tonica_ext:
        return out
    modo_ext = (modo_ext or "major").lower()
    out["externa"] = f"{tonica_ext} {modo_ext}"

    a, b = PITCH.get(tonica_det), PITCH.get(tonica_ext.replace("b", "b"))
    if a is None or b is None:
        return out
    if a == b and modo_det == modo_ext:
        out["veredicto"] = "coincide"
    elif (modo_det == "major" and modo_ext == "minor" and (b - a) % 12 == 9) or \
         (modo_det == "minor" and modo_ext == "major" and (a - b) % 12 == 9):
        out["veredicto"] = "relativa (mismas notas)"
    elif a == b:
        out["veredicto"] = "misma tonica, distinto modo"
    else:
        out["veredicto"] = "discrepan"
    return out
