"""Parseo de hojas acordes-sobre-letra y contraste con el analisis del audio.

Acepta cifrado americano y notacion latina (LA7, DO#m), que es como escribe
LaCuerda. La hoja final que usa esto es `pagina.py` (cancion.html).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .analyze import Analysis
from .lacuerda import a_americano

PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Sufijo tal como se escribe -> calidad interna (las claves de SUF en sheet.py).
# El orden importa: se prueba el mas largo primero.
SUFIJOS: list[tuple[str, str]] = [
    ("maj7", "maj7"), ("maj9", "maj9"), ("add9", "add9"), ("m7b5", "min7b5"),
    ("mmaj7", "minmaj7"), ("dim7", "dim7"), ("sus2", "sus2"), ("sus4", "sus4"),
    ("min7", "min7"), ("min6", "min6"), ("min9", "min9"), ("min", "min"),
    ("dim", "dim"), ("aug", "aug"), ("m7", "min7"), ("m6", "min6"),
    ("m9", "min9"), ("M7", "maj7"), ("m", "min"), ("sus", "sus4"),
    ("7", "7"), ("9", "9"), ("6", "6"), ("5", "5"), ("", "maj"),
]

_ACORDE_RE = re.compile(
    r"^(DO|RE|MI|FA|SOL|LA|SI|[A-G])([#b]?)([^/\s]*)(?:/(DO|RE|MI|FA|SOL|LA|SI|[A-G])([#b]?))?$",
    re.I)

# Un sufijo valido se arma solo con piezas reconocibles. Validar por conjunto
# de caracteres no alcanza: deja pasar cualquier permutacion, y entonces "Las"
# se lee como As y "Fui" como F — palabras de la letra convertidas en acordes.
_SUFIJO_OK = re.compile(
    r"^(?:maj|min|m|M|dim|aug|sus|add|[0-9]{1,2}|[b#][0-9]{1,2}|[()+\-])*$")

# Encabezados de seccion: (ESTROFA), [Coro], INTRO:
_SECCION_RE = re.compile(r"^\s*[\(\[]?\s*"
                         r"(intro|estrofa|verso|coro|estribillo|puente|bridge|"
                         r"solo|final|outro|interludio|pre.?coro|repetir)"
                         r"[^\n]*$", re.I)


@dataclass
class Acorde:
    """Un acorde ubicado en una columna de la linea de letra."""
    col: int
    texto: str              # como quedo escrito, ya en americano
    root: int | None        # semitono 0-11, None si no se pudo interpretar
    calidad: str            # clave de calidad para la digitacion
    bajo: int | None = None

    def to_dict(self) -> dict:
        return {"col": self.col, "t": self.texto, "r": self.root,
                "q": self.calidad, "b": self.bajo}


# Los transcriptores anotan a mano sobre el acorde: trastes entre corchetes
# (A[3- 2-3]), asteriscos de enfasis (E7*), guiones de duracion (Bm7 -) y
# aclaraciones de voicing (D(s3)9). Nada de eso cambia el acorde, pero si no se
# limpia, la linea entera deja de reconocerse y la hoja la trata como letra.
_SEPARADORES = {"-", "–", "—", "|", "/", "//", "*"}


def _limpiar_token(tok: str) -> str:
    """Saca las anotaciones manuales y deja el cifrado desnudo."""
    tok = re.sub(r"\[[^\]]*\]?", "", tok)      # trastes: A[3- 2-3]
    tok = re.sub(r"\((?:s\d|[^)]*)\)", "", tok)  # voicing: D(s3)9
    return tok.strip().strip(",*·.")


def parse_acorde(tok: str) -> Acorde | None:
    """Interpreta un token como acorde. Devuelve None si no lo es.

    Acepta notacion latina y americana. Lo que no entra en el vocabulario
    conocido se conserva como texto: se transpone la fundamental igual, pero
    no se dibuja diagrama.
    """
    tok = _limpiar_token(tok)
    if not tok:
        return None
    m = _ACORDE_RE.match(tok)
    if not m:
        return None
    raiz, alt, resto, braiz, balt = m.groups()

    americano = a_americano(raiz + (alt or ""))
    letra = americano[0].upper()
    if letra not in PITCH:
        return None
    root = (PITCH[letra] + (1 if "#" in americano else 0)
            - (1 if "b" in americano else 0)) % 12

    resto = resto or ""
    # Descarta palabras que empiezan con nota pero son letra ("Me", "Solo").
    if resto and not _SUFIJO_OK.match(resto):
        return None

    calidad = "maj"
    for escrito, clave in SUFIJOS:
        if escrito and resto.startswith(escrito):
            calidad = clave
            break
        if not escrito and not resto:
            calidad = "maj"

    bajo = None
    if braiz:
        bam = a_americano(braiz + (balt or ""))
        bl = bam[0].upper()
        if bl in PITCH:
            bajo = (PITCH[bl] + (1 if "#" in bam else 0)
                    - (1 if "b" in bam else 0)) % 12

    texto = americano + resto + (f"/{a_americano(braiz + (balt or ''))}" if braiz else "")
    return Acorde(col=0, texto=texto, root=root, calidad=calidad, bajo=bajo)


# Marcas que acompanan a los acordes sin ser acordes: repeticiones (x2, 2x),
# barras de compas y restos de anotaciones de trastes ("2-3]", "7M").
_IGNORABLE = re.compile(r"^(?:\(?[x×]\d+\)?|\(?\d+[x×]\)?|\|+:?|:?\|+|:|"
                        r"[\d\-\[\]M.]+|\(|\)|\.\.\.?)$", re.I)


def _trozos(linea: str) -> list[tuple[int, str | None]]:
    """Tokens de una linea con su columna. El texto es None si es ignorable.

    Separa los acordes encadenados con guion ("Bm-G-D-A") y los agrupados
    entre parentesis ("(Bm-G-D-A)"), dos convenciones muy comunes en LaCuerda:
    sin esto, toda la linea se leia como letra y la hoja quedaba sin acordes.
    """
    out: list[tuple[int, str | None]] = []
    for m in re.finditer(r"\S+", linea):
        tok, col = m.group(0), m.start()
        if tok in _SEPARADORES or _IGNORABLE.match(tok):
            out.append((col, None))
            continue
        # "A(4)" o "Bm(x2)": la cifra entre parentesis al final es una repeticion.
        tok = re.sub(r"(?<=\S)\((?:[x×])?\d+(?:[x×])?\)$", "", tok, flags=re.I)
        nucleo = tok.strip("()|")
        desde = col + tok.index(nucleo[0]) if nucleo else col
        if "-" in nucleo and "[" not in nucleo:
            partes = nucleo.split("-")
            llenas = [x for x in partes if x]
            if llenas and all(parse_acorde(x) for x in llenas):
                pos = 0
                for x in partes:
                    if x:
                        out.append((desde + pos, x))
                    pos += len(x) + 1
                continue
        if nucleo != tok and parse_acorde(nucleo):
            out.append((desde, nucleo))
            continue
        out.append((col, tok))
    return out


def es_linea_de_acordes(linea: str) -> bool:
    """True si la linea es solo acordes (la que va encima de la letra).

    Se toleran guiones, barras, repeticiones (x2) y parentesis, pero tiene que
    haber al menos un acorde de verdad: una linea de puros separadores no es
    una linea de acordes.
    """
    toks = [t for _, t in _trozos(linea) if t is not None]
    if not toks or len(toks) > 24:
        return False
    return all(parse_acorde(t) for t in toks)


def _acordes_de_linea(linea: str, desplazamiento: int = 0) -> list[Acorde]:
    out = []
    for col, t in _trozos(linea):
        if t is None:
            continue
        ac = parse_acorde(t)
        if ac:
            ac.col = col + desplazamiento
            out.append(ac)
    return out


def parse_letra(texto: str) -> list[dict]:
    """Convierte el texto acordes-sobre-letra en lineas estructuradas.

    Cada linea de salida es una de: seccion, par acorde+letra, letra sola,
    acordes solos (riffs, intro) o blanco.
    """
    out: list[dict] = []
    lineas = texto.replace("\t", "    ").split("\n")
    i = 0
    while i < len(lineas):
        cruda = lineas[i].rstrip()

        if not cruda.strip():
            out.append({"tipo": "blanco"})
            i += 1
            continue

        if es_linea_de_acordes(cruda):
            acordes = _acordes_de_linea(cruda)
            # La linea siguiente es letra solo si no es a su vez acordes.
            sig = lineas[i + 1].rstrip() if i + 1 < len(lineas) else ""
            if sig.strip() and not es_linea_de_acordes(sig):
                out.append({"tipo": "par", "ac": [a.to_dict() for a in acordes],
                            "txt": sig})
                i += 2
            else:
                out.append({"tipo": "acordes", "ac": [a.to_dict() for a in acordes],
                            "txt": ""})
                i += 1
            continue

        if _SECCION_RE.match(cruda) and len(cruda.strip()) < 60:
            # "Intro: MI LA7 SI7" trae etiqueta y acordes en la misma linea.
            # Si no se separan, esos acordes quedan como texto y no transponen.
            etiqueta, sep, cola = cruda.partition(":")
            if sep and cola.strip() and es_linea_de_acordes(cola):
                out.append({"tipo": "seccion", "txt": etiqueta.strip(" ()[]")})
                acordes = _acordes_de_linea(cola, len(etiqueta) + 1)
                out.append({"tipo": "acordes",
                            "ac": [a.to_dict() for a in acordes], "txt": ""})
            else:
                out.append({"tipo": "seccion", "txt": cruda.strip(" ()[]")})
            i += 1
            continue

        out.append({"tipo": "letra", "txt": cruda})
        i += 1

    return out


def _vocabulario(lineas: list[dict]) -> list[dict]:
    """Acordes unicos de la hoja, en orden de aparicion."""
    vistos, out = set(), []
    for l in lineas:
        for a in l.get("ac", []):
            if a["r"] is None:
                continue
            k = (a["r"], a["q"])
            if k not in vistos:
                vistos.add(k)
                out.append({"r": a["r"], "q": a["q"]})
    return out


# Para alinear dos fuentes alcanza con saber si el acorde es mayor, menor o
# algo mas: un Eb y un Eb7 cumplen la misma funcion frente a una transcripcion.
CLASE = {"min": "m", "min7": "m", "min6": "m", "min9": "m", "minmaj7": "m",
         "min7b5": "m", "dim": "m", "dim7": "m"}


def _clase(q: str) -> str:
    return CLASE.get(q, "M")


def comparar(voc: list[dict], a: Analysis) -> dict:
    """Contrasta los acordes de la transcripcion con los del analisis propio.

    Busca el desplazamiento global en semitonos que mejor alinea los dos
    conjuntos. Un offset distinto de cero suele significar que la grabacion
    esta afinada medio tono abajo, o que es una version en otra tonalidad —
    no necesariamente que el analisis se haya equivocado.

    El puntaje es simetrico a proposito. Mirando solo cuanto tiempo del audio
    queda explicado, gana el acorde mas largo del tema y cualquier
    desplazamiento que lo haga coincidir con algo — aunque deje el resto de la
    hoja sin explicar. Exigir que tambien se expliquen los acordes de la hoja
    descarta esas coincidencias de un solo acorde.
    """
    propios: dict[tuple[int, str], float] = {}
    for c in a.chords:
        if c.get("root") is None:
            continue
        dur = float(c.get("end", 0)) - float(c.get("start", 0))
        propios[(c["root"], c["quality"])] = propios.get(
            (c["root"], c["quality"]), 0.0) + dur
    total = sum(propios.values()) or 1.0

    if not voc or not propios:
        return {"offset": 0, "cobertura": 0.0, "detalle": []}

    hoja = {(v["r"], _clase(v["q"])) for v in voc}

    def puntuar(off: int) -> tuple[float, float, float]:
        """Devuelve (puntaje, % del audio explicado, % de la hoja explicada)."""
        audio = sum(d for (r, q), d in propios.items()
                    if ((r + off) % 12, _clase(q)) in hoja)
        cubiertos = sum(1 for (r, cl) in hoja
                        if any((r0 + off) % 12 == r and _clase(q0) == cl
                               for (r0, q0) in propios))
        ra = audio / total
        rh = cubiertos / len(hoja)
        # Media armonica: un offset tiene que explicar las dos fuentes a la vez.
        f = 0.0 if ra + rh == 0 else 2 * ra * rh / (ra + rh)
        return f, ra, rh

    # En empate gana el desplazamiento mas chico: medio tono por afinacion es
    # muchisimo mas frecuente que un tritono.
    candidatos = sorted(range(12), key=lambda o: min(o, 12 - o))
    mejor = max(candidatos, key=lambda o: (round(puntuar(o)[0], 4),
                                           -min(o, 12 - o)))
    f, ra, rh = puntuar(mejor)

    detalle = []
    for (r, q), d in sorted(propios.items(), key=lambda kv: -kv[1])[:8]:
        destino = (r + mejor) % 12
        detalle.append({"r": r, "q": q, "peso": round(100 * d / total, 1),
                        "mapea": destino,
                        "en_hoja": (destino, _clase(q)) in hoja})

    return {"offset": mejor if mejor <= 6 else mejor - 12,
            "cobertura": round(100 * ra, 1),
            "cobertura_hoja": round(100 * rh, 1),
            "puntaje": round(100 * f, 1),
            "detalle": detalle}
