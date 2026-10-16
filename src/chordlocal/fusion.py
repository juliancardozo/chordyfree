"""Fusion del analisis del audio con lo encontrado online.

Tres operaciones, todas instantaneas porque reusan el croma ya guardado:

  * `refinar`: vuelve a decodificar los acordes del audio con un prior armado
    con el vocabulario de la transcripcion (llevado al tono de la grabacion).
    El audio sigue decidiendo *cuando* cambia el acorde y si un acorde de la
    hoja suena o no; la transcripcion solo desempata entre lecturas que el
    croma deja parejas (E vs G#m, A vs C#m), que es justo donde el detector
    mas se equivoca.
  * `acordes_sobre_letra`: con una letra sincronizada (LRCLIB), ubica cada
    acorde detectado sobre la silaba que se canta en ese momento.
  * `tiempos_de_hoja`: asigna a cada linea de la transcripcion el tiempo en que
    se canta, emparejando su texto con la letra sincronizada.

Y `analisis`, que redacta el analisis musical de la cancion.
"""
from __future__ import annotations

import difflib
from collections import Counter

import numpy as np

from .harmony import (BORROWED_IN_MAJOR, BORROWED_IN_MINOR, MAJOR_DIATONIC,
                      MINOR_DIATONIC, Key, roman_numeral)
from .lacuerda import _norm
from .playalong import _clase
from .recognize import VOCAB_QUALITIES, decode, state_labels
from .theory import FLAT, PITCH, SHARP, Chord


# --------------------------------------------------------------------------
# Re-decodificacion guiada por la transcripcion
# --------------------------------------------------------------------------

def _calidad_vocab(q: str) -> str:
    """Lleva una calidad de la hoja a la mas cercana del vocabulario del decodificador."""
    if q in VOCAB_QUALITIES:
        return q
    return {"6": "maj", "9": "7", "maj9": "maj7", "add9": "maj", "5": "maj",
            "aug": "maj", "min6": "min", "min9": "min7", "minmaj7": "min",
            "dim7": "dim"}.get(q, "maj" if _clase(q) == "M" else "min")


def prior_de_hoja(voc: list[dict], offset: int) -> np.ndarray:
    """Prior sobre los estados del decodificador a partir de la hoja.

    `offset` es el de `playalong.comparar`: acorde_hoja = acorde_audio + offset.
    """
    labels = state_labels()
    exactos = {((v["r"] - offset) % 12, _calidad_vocab(v["q"])) for v in voc}
    clases = {((v["r"] - offset) % 12, _clase(v["q"])) for v in voc}
    raices = {r for r, _ in clases}
    prior = np.zeros(len(labels), dtype=np.float32)
    for i, c in enumerate(labels):
        if c is None:
            continue
        if (c.root, c.quality) in exactos:
            prior[i] = 0.9
        elif (c.root, _clase(c.quality)) in clases:
            prior[i] = 0.6
        elif c.root in raices:
            prior[i] = -0.2
        else:
            prior[i] = -0.8
    return prior


def refinar(a, voc: list[dict], offset: int) -> list[dict] | None:
    """Acordes del audio re-decididos con la hoja. None si no hay croma guardado."""
    if not a.chroma or not a.beats or not voc:
        return None
    chroma = np.asarray(a.chroma, dtype=np.float32)
    bass = (np.asarray(a.bass, dtype=np.float32) if a.bass
            else np.zeros_like(chroma))
    beats = np.array([b["t"] for b in a.beats], dtype=float)
    downs = np.array([b["t"] for b in a.beats if b.get("downbeat")], dtype=float)
    n = min(len(chroma), len(beats) - 1)
    if n < 2:
        return None
    spans = decode(chroma[:n], bass[:n], beats[:n + 1], downs,
                   key_prior=prior_de_hoja(voc, offset))
    flats = (a.key or {}).get("flats", False)
    return [{"start": round(s.start, 3), "end": round(s.end, 3),
             "label": s.chord.name(flats) if s.chord else "N",
             "root": s.chord.root if s.chord else None,
             "quality": s.chord.quality if s.chord else None,
             "bar": s.bar, "beat": s.beat} for s in spans]


# --------------------------------------------------------------------------
# Letra sincronizada + acordes del audio
# --------------------------------------------------------------------------

def _acorde_dict(c: dict, col: int) -> dict:
    return {"col": col, "t": c["label"], "r": c["root"], "q": c["quality"], "b": None,
            "s": round(c["start"], 2)}


def _columna(txt: str, frac: float) -> int:
    """Columna al inicio de la palabra mas cercana a la fraccion dada de la linea."""
    if not txt:
        return 0
    obj = int(round(frac * len(txt)))
    inicios = [0] + [i + 1 for i, ch in enumerate(txt) if ch == " " and i + 1 < len(txt)]
    return min(inicios, key=lambda i: abs(i - obj))


def acordes_sobre_letra(sinc: list[dict], chords: list[dict], fin: float) -> list[dict]:
    """Lineas tipo `playalong` (par / acordes / blanco) a partir de letra con tiempos.

    Cada linea se canta entre su marca y la siguiente, pero no mas de lo que
    razonablemente dura cantarla: si hay un instrumental en el medio, sus
    acordes van en una linea propia en vez de amontonarse al final del verso.
    """
    cs = [c for c in chords if c.get("root") is not None]
    out: list[dict] = []

    def acordes_entre(t0: float, t1: float) -> list[dict]:
        return [c for c in cs if c["end"] > t0 + 0.05 and c["start"] < t1 - 0.05]

    # Intro: lo que suena antes de la primera linea cantada.
    primero = sinc[0]["t"] if sinc else fin
    intro = acordes_entre(0, primero)
    if intro and primero > 3:
        out.append({"tipo": "seccion", "txt": "Intro"})
        out.append({"tipo": "acordes", "t0": 0.0, "t1": primero,
                    "ac": [_acorde_dict(c, 0) for c in _sin_repetir(intro)], "txt": ""})

    for i, l in enumerate(sinc):
        t0 = l["t"]
        sig = sinc[i + 1]["t"] if i + 1 < len(sinc) else fin
        txt = l["txt"]
        if not txt.strip():
            out.append({"tipo": "blanco", "t0": t0})
            continue
        canto = min(sig, t0 + max(2.5, min(9.0, 0.11 * len(txt) + 1.0)))
        ac = []
        for c in acordes_entre(t0, canto):
            frac = max(0.0, (c["start"] - t0) / max(0.1, canto - t0))
            col = _columna(txt, frac)
            if ac and ac[-1]["col"] >= col:
                # Dos cambios dentro de la misma palabra: se corre al siguiente
                # espacio libre para no encimar los nombres.
                col = ac[-1]["col"] + len(ac[-1]["t"]) + 1
            if ac and ac[-1]["t"] == c["label"]:
                continue
            ac.append(_acorde_dict(c, col))
        out.append({"tipo": "par", "ac": ac, "txt": txt, "t0": t0, "t1": canto})

        # Instrumental entre esta linea y la siguiente.
        if sig - canto > 4.0:
            resto = _sin_repetir(acordes_entre(canto, sig))
            if resto:
                out.append({"tipo": "acordes", "t0": canto, "t1": sig,
                            "ac": [_acorde_dict(c, 0) for c in resto], "txt": ""})
    # Columna real para las lineas de solo acordes: separados por dos espacios.
    for l in out:
        if l["tipo"] == "acordes":
            col = 0
            for a in l["ac"]:
                a["col"] = col
                col += len(a["t"]) + 2
    return out


def _sin_repetir(cs: list[dict]) -> list[dict]:
    out = []
    for c in cs:
        if not out or out[-1]["label"] != c["label"]:
            out.append(c)
    return out


def tiempos_de_hoja(lineas: list[dict], sinc: list[dict], umbral: float = 0.62) -> int:
    """Asigna `t0` a las lineas de la hoja que matchean con la letra sincronizada.

    Emparejamiento monotono: cada linea de la hoja busca hacia adelante, en una
    ventana corta, la linea sincronizada mas parecida. Devuelve cuantas lineas
    quedaron con tiempo.
    """
    norm = [_norm(s["txt"]) for s in sinc]
    j = 0
    n = 0
    for l in lineas:
        if l["tipo"] not in ("par", "letra") or not l.get("txt", "").strip():
            continue
        objetivo = _norm(l["txt"])
        if len(objetivo) < 4:
            continue
        mejor, mk = 0.0, -1
        for k in range(j, min(len(sinc), j + 10)):
            r = difflib.SequenceMatcher(None, objetivo, norm[k]).ratio()
            if r > mejor:
                mejor, mk = r, k
        if mk >= 0 and mejor >= umbral:
            l["t0"] = sinc[mk]["t"]
            j = mk + 1
            n += 1
    return n


# --------------------------------------------------------------------------
# Analisis musical en texto
# --------------------------------------------------------------------------

FUNCION = {0: "tónica", 2: "subdominante", 4: "tónica", 5: "subdominante",
           7: "dominante", 9: "tónica", 11: "dominante"}
FUNCION_MENOR = {0: "tónica", 2: "subdominante", 3: "tónica", 5: "subdominante",
                 7: "dominante", 8: "subdominante", 10: "dominante", 11: "dominante"}

PATRONES = [
    (("I", "V", "vi", "IV"), "la progresión pop por excelencia (I–V–vi–IV)"),
    (("vi", "IV", "I", "V"), "la variante menor de la progresión pop (vi–IV–I–V)"),
    (("I", "vi", "IV", "V"), "la progresión de los años 50 (I–vi–IV–V)"),
    (("I", "IV", "V"), "el I–IV–V del blues, el rock and roll y el folk"),
    (("ii", "V", "I"), "un ii–V–I, la cadencia del jazz"),
    (("i", "VII", "VI", "V"), "la cadencia andaluza (i–VII–VI–V)"),
    (("i", "VI", "III", "VII"), "el i–VI–III–VII, muy común en pop/rock en menor"),
    (("I", "bVII", "IV"), "el I–bVII–IV, préstamo mixolidio típico del rock"),
    (("i", "iv", "v"), "el i–iv–v, la versión menor del I–IV–V"),
    (("i", "iv", "V"), "el i–iv–V del menor armónico"),
]


def key_de_acordes(chords: list[dict], a) -> Key:
    """Re-estima la tonalidad sobre una secuencia de acordes (p. ej. refinada)."""
    from .harmony import estimate_key
    from .recognize import ChordSpan
    spans = [ChordSpan(start=c["start"], end=c["end"],
                       chord=Chord(c["root"], c["quality"]) if c.get("root") is not None else None)
             for c in chords]
    chroma = np.asarray(a.chroma, dtype=np.float32) if a.chroma else None
    return estimate_key(spans, chroma)


# Ortografia por tonalidad. En Do mayor / La menor las alteraciones habituales
# son mixtas: Bb, Eb y Ab (prestamos del menor) pero F# y C# (dominantes
# secundarias). Con una sola tabla de sostenidos aparecia "A#" en La menor.
_NATURAL = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def nombres_para(key: Key) -> list[str]:
    rel = key.tonic if key.mode == "major" else (key.tonic + 3) % 12
    if rel in (5, 10, 3, 8, 1, 6):
        return FLAT
    if rel == 0:
        return _NATURAL
    return SHARP


def nombre_acorde(root: int, quality: str, key: Key) -> str:
    from .theory import PRINT_SUFFIX
    return nombres_para(key)[root % 12] + PRINT_SUFFIX.get(quality, "")


def _key_de(a) -> Key:
    k = a.key or {}
    tonic = PITCH.get(k.get("tonic", "C"), 0)
    return Key(tonic=tonic, mode=k.get("mode", "major"), confidence=k.get("confidence", 0))


def _en_escala(ch: Chord, key: Key, escala: set[int]) -> bool:
    """Diatonico, contando en menor la dominante de la menor armonica (V, V7, vii°)."""
    if set(ch.pitches()) <= escala:
        return True
    if key.mode == "minor":
        armonica = escala | {(key.tonic + 11) % 12}
        return set(ch.pitches()) <= armonica and (ch.root - key.tonic) % 12 in (7, 11)
    return False


def _secuencia(chords: list[dict]) -> list[dict]:
    seq = []
    for c in chords:
        if c.get("root") is None:
            continue
        if seq and seq[-1]["root"] == c["root"] and seq[-1]["quality"] == c["quality"]:
            seq[-1]["dur"] += c["end"] - c["start"]
            continue
        seq.append({"root": c["root"], "quality": c["quality"], "label": c["label"],
                    "dur": c["end"] - c["start"]})
    return seq


def _sin_extension(rn: str) -> str:
    """'V7/IV' -> 'V7/IV' (se respeta); 'IVmaj7' -> 'IV'; 'ii7' -> 'ii'."""
    if "/" in rn or rn.startswith("("):
        return rn
    for suf in ("maj7", "ø7", "°7", "sus4", "sus2", "7"):
        if rn.endswith(suf):
            return rn[: -len(suf)]
    return rn


def _bucle_principal(romanos: list[str], durs: list[float]) -> tuple[list[str], int] | None:
    """El ciclo de 3-4 acordes que mas tiempo ocupa en la cancion."""
    mejor = None
    for n in (4, 3):
        cuenta: Counter = Counter()
        for i in range(len(romanos) - n + 1):
            g = tuple(romanos[i:i + n])
            if len(set(g)) < 2:
                continue
            cuenta[g] += 1
        if not cuenta:
            continue
        g, k = cuenta.most_common(1)[0]
        if k >= 2 and (mejor is None or k * n > mejor[1] * len(mejor[0]) * 1.2):
            mejor = (list(g), k)
    return mejor


def analisis(a, chords: list[dict] | None = None, hoja: dict | None = None,
             fuentes: dict | None = None, key: Key | None = None) -> dict:
    """Arma el analisis de la cancion: datos y parrafos listos para mostrar."""
    chords = chords or a.chords
    key = key or _key_de(a)
    flats = key.flats
    nombres = nombres_para(key)
    arm = a.armonia or {}
    seq = _secuencia(chords)
    total = sum(s["dur"] for s in seq) or 1.0
    escala = set(key.scale())
    func = FUNCION if key.mode == "major" else FUNCION_MENOR

    # --- tabla de acordes ---
    por_acorde: dict[tuple, dict] = {}
    for s in seq:
        k = (s["root"], s["quality"])
        ch = Chord(root=s["root"], quality=s["quality"])
        d = por_acorde.setdefault(k, {
            "label": nombre_acorde(ch.root, ch.quality, key),
            "romano": roman_numeral(ch, key), "dur": 0.0,
            "veces": 0, "diatonico": _en_escala(ch, key, escala),
            "funcion": func.get((s["root"] - key.tonic) % 12, "color / préstamo")})
        d["dur"] += s["dur"]
        d["veces"] += 1
    tabla = sorted(por_acorde.values(), key=lambda d: -d["dur"])
    for d in tabla:
        d["pct"] = round(100 * d["dur"] / total, 1)
        d["dur"] = round(d["dur"], 1)

    # --- progresion principal y patrones ---
    romanos = [_sin_extension(roman_numeral(Chord(s["root"], s["quality"]), key))
               for s in seq]
    bucle = _bucle_principal(romanos, [s["dur"] for s in seq])
    texto_rom = " ".join(romanos)
    patrones = []
    for pat, desc in PATRONES:
        if " ".join(pat) in texto_rom:
            patrones.append(desc)

    # --- cadencias ---
    cad = Counter()
    for x, y in zip(seq, seq[1:]):
        dx, dy = (x["root"] - key.tonic) % 12, (y["root"] - key.tonic) % 12
        if dy != 0:
            continue
        if dx == 7 and x["quality"] in ("maj", "7"):
            cad["auténtica (V→I)"] += 1
        elif dx == 5:
            cad["plagal (IV→I)"] += 1
        elif dx == 10:
            cad["mixolidia (bVII→I)"] += 1
        elif dx == 11:
            cad["por sensible (vii→I)"] += 1

    funciones = Counter()
    for s in seq:
        funciones[func.get((s["root"] - key.tonic) % 12, "color / préstamo")] += s["dur"]
    reparto = {k: round(100 * v / total) for k, v in funciones.most_common()}

    fuera = [d for d in tabla if not d["diatonico"] and d["pct"] >= 1.0]

    # --- dificultad ---
    from .theory import fingering
    cejillas = [d["label"] for d in tabla if d["pct"] >= 2.0
                and (f := fingering(Chord.parse(d["label"]) or Chord(0))) and f.base > 0]
    from .harmony import suggest_capo
    capo = suggest_capo(key)

    # --- parrafos ---
    p: list[str] = []
    modo = "mayor" if key.mode == "major" else "menor"
    rel = (key.tonic + (9 if key.mode == "major" else 3)) % 12
    p.append(f"La canción está en <b>{nombres[key.tonic]} {modo}</b> "
             f"(escala {' '.join(nombres[x] for x in key.scale())}); su relativa es "
             f"{nombres[rel]} {'menor' if key.mode == 'major' else 'mayor'}. "
             f"Usa {len(tabla)} acordes distintos.")
    if bucle:
        g, k = bucle
        p.append(f"El ciclo que más se repite es <b>{' – '.join(g)}</b> "
                 f"({k} veces).")
    if patrones:
        p.append("Reconocible: " + "; ".join(patrones[:3]) + ".")
    if cad:
        p.append("Cadencias hacia la tónica: " +
                 ", ".join(f"{k} ×{v}" for k, v in cad.most_common()) + ".")
    if reparto:
        p.append("Reparto funcional del tiempo: " +
                 ", ".join(f"{k} {v}%" for k, v in reparto.items()) + ".")
    if fuera:
        p.append("Acordes fuera de la escala (color armónico): " +
                 ", ".join(f"{d['label']} ({d['romano']})" for d in fuera[:6]) + ".")
    tempo = a.tempo or {}
    if tempo.get("bpm"):
        p.append(f"Pulso de {tempo['bpm']} BPM en {tempo.get('meter', '4/4')}.")
    if cejillas:
        txt = f"Pide cejilla en {', '.join(cejillas[:5])}."
        if capo:
            txt += (f" Con capo en el traste {capo['capo']} se toca con formas de "
                    f"{capo['forma']}.")
        p.append(txt)
    elif tabla:
        p.append("Se toca entero con acordes en posición abierta.")

    # --- concordancia con la transcripcion ---
    if hoja and hoja.get("comparacion"):
        c = hoja["comparacion"]
        frase = (f"La transcripción online concuerda un <b>{c.get('puntaje', 0)}%</b> con lo "
                 f"que se escucha en el audio.")
        if c.get("offset"):
            frase += (f" Está escrita {abs(c['offset'])} semitono"
                      f"{'s' if abs(c['offset']) != 1 else ''} "
                      f"{'arriba' if c['offset'] > 0 else 'abajo'} de la grabación "
                      "(afinación distinta, o la transcripción usa otra tonalidad); "
                      "la hoja se muestra ya llevada al tono que suena.")
        p.append(frase)

    return {"tonalidad": f"{nombres[key.tonic]} {modo}", "flats": key.flats,
            "tonica": key.tonic, "modo": key.mode, "parrafos": p, "tabla": tabla, "bucle": bucle[0] if bucle else [],
            "patrones": patrones, "cadencias": dict(cad), "reparto": reparto,
            "secciones": _secciones(hoja)}


def _secciones(hoja: dict | None) -> list[str]:
    if not hoja:
        return []
    return [l["txt"] for l in hoja.get("lineas", []) if l["tipo"] == "seccion"]
