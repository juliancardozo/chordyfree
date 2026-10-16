"""Verificacion del parseo de hojas acordes-sobre-letra y del ranking de busqueda.

No toca la red: el ranking se prueba con candidatos armados a mano y el parseo
con texto de relleno, no con transcripciones reales.
"""
import sys
sys.path.insert(0, "src")

from chordlocal.lacuerda import (Candidato, a_americano, limpiar_titulo,
                                 rankear, resumen_acordes)
from chordlocal.playalong import (comparar, es_linea_de_acordes, parse_acorde,
                                  parse_letra)

fails = []
def check(desc, got, want):
    if got != want:
        fails.append(f"{desc}: obtuvo {got!r}, esperaba {want!r}")


# --- notacion latina -> americana ---
for latino, americano in [("MI", "E"), ("LA7", "A7"), ("DO#m", "C#m"),
                          ("SOL", "G"), ("SIb", "Bb"), ("RE9", "D9"),
                          ("FA#m7", "F#m7"), ("MI/SOL#", "E/G#"),
                          ("DOsus4", "Csus4")]:
    check(f"latino {latino}", a_americano(latino), americano)

# El cifrado americano tiene que pasar sin cambios.
for ya in ["C", "Am", "G7", "F#m7b5", "Bb", "E/G#"]:
    check(f"americano {ya} intacto", a_americano(ya), ya)

# En el campo `odes` el sitio escribe el sostenido como arroba.
check("sostenido como @", a_americano("F@"), "F#")
check("odes completo", resumen_acordes(Candidato(acordes="MI LA7 SI7")),
      ["E", "A7", "B7"])


# --- parseo de un acorde suelto ---
check("MI es acorde", parse_acorde("MI").root, 4)
check("LA7 calidad", parse_acorde("LA7").calidad, "7")
check("DO#m calidad", parse_acorde("DO#m").calidad, "min")
check("MI/SOL# bajo", parse_acorde("MI/SOL#").bajo, 8)
check("Am7 calidad", parse_acorde("Am7").calidad, "min7")
check("Cmaj7 calidad", parse_acorde("Cmaj7").calidad, "maj7")

# Palabras de letra que empiezan como nota no deben pasar por acorde.
for palabra in ["Me", "Solo", "Las", "Fui", "Dame", "Si", "Mi"]:
    if palabra in ("Si", "Mi"):
        continue          # ambiguos de verdad: son nota y palabra a la vez
    check(f"{palabra!r} no es acorde", parse_acorde(palabra), None)


# --- clasificacion de lineas ---
check("linea de acordes", es_linea_de_acordes("MI      LA7   SI7"), True)
check("linea de letra", es_linea_de_acordes("lorem ipsum dolor sit"), False)
check("linea vacia", es_linea_de_acordes(""), False)


# Acordes encadenados con guion y grupos entre parentesis (convencion de LaCuerda).
check("encadenados", es_linea_de_acordes("Bm-G-D-A (x2)"), True)
check("grupo entre parentesis", es_linea_de_acordes("Bm      G     (Bm-G-D-A)"), True)
check("columnas encadenadas",
      [(a["t"], a["col"]) for a in parse_letra("Bm-G-D-A")[0]["ac"]],
      [("Bm", 0), ("G", 3), ("D", 5), ("A", 7)])
check("repeticion pegada al acorde",
      [a["t"] for a in parse_letra("Bm-G-D-A(4)")[0]["ac"]], ["Bm", "G", "D", "A"])
check("anotacion de trastes", es_linea_de_acordes("F#m[8-    7M    7]  B7"), True)
check("letra con guion no es acordes", es_linea_de_acordes("lorem ipsum - dolor"), False)

# --- parseo de la hoja ---
HOJA = """Intro: MI  LA7

(ESTROFA)
MI            LA7
lorem ipsum dolor
SI7
sit amet
"""
lineas = parse_letra(HOJA)
tipos = [l["tipo"] for l in lineas]
check("seccion con acordes se separa", tipos[:2], ["seccion", "acordes"])
check("estrofa detectada", "seccion" in tipos[2:], True)
check("hay dos pares acorde+letra", sum(1 for t in tipos if t == "par"), 2)

par = next(l for l in lineas if l["tipo"] == "par")
check("acordes del par", [a["t"] for a in par["ac"]], ["E", "A7"])
check("columna del segundo acorde", par["ac"][1]["col"], 14)
check("letra del par", par["txt"], "lorem ipsum dolor")

# Los acordes de la linea "Intro:" conservan su columna real en la linea.
intro = lineas[1]
check("columnas de la intro", [a["col"] for a in intro["ac"]], [7, 11])


# --- limpieza de titulos de YouTube ---
check("titulo con parentesis",
      limpiar_titulo("Juan Wauters - Para hacer música, para hacer "
                     "(Miguel Y El Comité) (Live on PardelionMusic.tv)"),
      ("Para hacer música, para hacer", "Juan Wauters"))
check("titulo con pipe",
      limpiar_titulo("Artista - Tema | Sello Oficial"), ("Tema", "Artista"))
check("titulo con feat",
      limpiar_titulo("Alguien ft. Otro - Tema")[1], "Alguien")


# --- ranking: el buscador del sitio es laxo y hay que ordenar lo que vuelve ---
cands = [
    Candidato(artista="Juan y Juan", tema="Balada para un gordo"),
    Candidato(artista="Montevideo Blues", tema="Para hacer música, para hacer"),
    Candidato(artista="Música de TV", tema="South Park - voy a hacer el bien"),
]
ranked = rankear(cands, "Para hacer música, para hacer", "Miguel Y El Comité")
check("el titulo exacto queda primero", ranked[0].artista, "Montevideo Blues")
# Los dos resultados de relleno empatan (comparten una sola palabra cada uno);
# lo que importa es que queden claramente por debajo del match real.
check("el match real se despega del ruido",
      ranked[0].puntaje > 2 * max(c.puntaje for c in ranked[1:]), True)


# --- alineacion entre la hoja y el analisis ---
class FakeAnalysis:
    """Analisis minimo: solo los acordes con su duracion."""
    def __init__(self, chords):
        self.chords = chords

def acorde(root, quality, dur):
    return {"root": root, "quality": quality, "start": 0.0, "end": dur}

# Audio en Eb (Eb-Ab7-Bb) contra una hoja en E (E-A7-B7): medio tono.
audio_eb = FakeAnalysis([acorde(3, "maj", 40), acorde(8, "7", 30),
                         acorde(10, "maj", 20)])
hoja_e = [{"r": 4, "q": "maj"}, {"r": 9, "q": "7"}, {"r": 11, "q": "7"}]
c = comparar(hoja_e, audio_eb)
check("desfase de medio tono", c["offset"], 1)
check("explica toda la hoja", c["cobertura_hoja"], 100.0)

# Misma tonalidad: no debe inventar desplazamiento.
audio_e = FakeAnalysis([acorde(4, "maj", 40), acorde(9, "7", 30),
                        acorde(11, "7", 20)])
check("sin desfase", comparar(hoja_e, audio_e)["offset"], 0)

# Un solo acorde largo coincidiendo no alcanza para mover todo el tono: el
# puntaje simetrico tiene que descartar esa coincidencia barata.
audio_sesgado = FakeAnalysis([acorde(7, "min", 300), acorde(3, "maj", 10)])
c2 = comparar(hoja_e, audio_sesgado)
check("no sigue al acorde dominante solo", c2["cobertura_hoja"] < 100.0, True)

# Hoja vacia: no hay nada que comparar y no debe explotar.
check("hoja vacia", comparar([], audio_e)["offset"], 0)


print(f"\nverificaciones fallidas: {len(fails)}")
for f in fails:
    print("  FALLO:", f)
print("OK" if not fails else "HAY FALLOS")
sys.exit(1 if fails else 0)
