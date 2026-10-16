"""Verificacion de fuentes online, fusion y el post-procesado de beats.

No toca la red: todo con HTML, letras y activaciones armadas a mano.
"""
import sys
sys.path.insert(0, "src")

import numpy as np

from chordlocal import beats as BT
from chordlocal.analyze import detect_track_end
from chordlocal.cifraclub import cifra_de_html, slug, url_probable
from chordlocal.fetch import normalize_url
from chordlocal.fusion import acordes_sobre_letra, prior_de_hoja, tiempos_de_hoja
from chordlocal.harmony import Key
from chordlocal.lacuerda import candidatos_de_html, transcripcion_de_html
from chordlocal.letras import Letra, parse_lrc, puntuar
from chordlocal.playalong import parse_letra
from chordlocal.recognize import state_labels

fails = []
def check(desc, got, want):
    if got != want:
        fails.append(f"{desc}: obtuvo {got!r}, esperaba {want!r}")


# --- LaCuerda: la transcripcion viene en t_body, con acordes en <A> ---
HTML_LC = """<div id=tH4><pre id='tCode'></pre></div>
<div id=t_body><PRE><div></div><A>MI</A>          <A>LA7</A>
lorem ipsum dolor sit
<div></div><A>SI7</A>
amet &amp; co
</PRE></div>"""
t = transcripcion_de_html(HTML_LC)
check("lacuerda: primera linea", t.splitlines()[0], "MI          LA7")
check("lacuerda: entidades", t.splitlines()[3], "amet & co")
check("lacuerda: alineacion conservada", t.splitlines()[0].index("LA7"), 12)
check("lacuerda: el tCode vacio no es la transcripcion", transcripcion_de_html(
    "<pre id='tCode'></pre>"), "")
lin = parse_letra(t)
check("lacuerda -> parse", [l["tipo"] for l in lin], ["par", "par"])

# --- LaCuerda: el emparejamiento artista/tema de la busqueda varia por pagina ---
INV = """function lOpen(n,v) { fn=''+hds[NMAX-n]+'/'+fns[n]; }
NMAX=2;
var hds=['kilombo','kilombo','mr_kilombo'];
var fns=['noseque','el_dia_s','yo_no_se'];"""
check("busqueda con NMAX-n", [(c.artista, c.tema) for c in candidatos_de_html(INV)][0],
      ("mr_kilombo", "noseque"))
DIR = INV.replace("hds[NMAX-n]", "hds[n]")
check("busqueda directa", [(c.artista, c.tema) for c in candidatos_de_html(DIR)][0],
      ("kilombo", "noseque"))

# --- Cifra Club ---
check("cifra: pre con <b>", cifra_de_html("<pre><b>Am</b>   <b>F</b>\nla letra</pre>"),
      "Am   F\nla letra")
check("slug con acentos", slug("Legião Urbana"), "legiao-urbana")
check("url probable", url_probable("Tempo Perdido", "Legião Urbana"),
      "https://www.cifraclub.com.br/legiao-urbana/tempo-perdido/")

# --- URLs de YouTube ---
check("radio de youtube", normalize_url(
    "https://www.youtube.com/watch?v=F0NNumCb3C4&list=RDF0NNumCb3C4&start_radio=1"),
    "https://www.youtube.com/watch?v=F0NNumCb3C4")
check("youtu.be con t", normalize_url("https://youtu.be/1OuxTIyqGUY?t=30"),
      "https://www.youtube.com/watch?v=1OuxTIyqGUY")
check("otros sitios intactos", normalize_url("https://vimeo.com/1"), "https://vimeo.com/1")

# --- LRCLIB ---
lrc = parse_lrc("[00:12.50] primera\n[01:02.00] segunda\nbasura\n[00:05.00]")
check("lrc tiempos", [x["t"] for x in lrc], [5.0, 12.5, 62.0])
check("lrc texto", lrc[1]["txt"], "primera")
buena = puntuar(Letra(tema="Tempo Perdido", artista="Legião Urbana", duracion=201,
                      texto="x", sincronizada=lrc), "Tempo Perdido", "Legiao Urbana", 200)
otra = puntuar(Letra(tema="Tempo", artista="Otro", duracion=320, texto="x"),
               "Tempo Perdido", "Legiao Urbana", 200)
check("la letra correcta gana", buena.puntaje > otra.puntaje + 4, True)

# --- Letra sincronizada + acordes del audio ---
sinc = [{"t": 10.0, "txt": "uno dos tres cuatro"}, {"t": 14.0, "txt": "cinco seis"},
        {"t": 30.0, "txt": "siete"}]
cs = [{"start": 0, "end": 10, "label": "C", "root": 0, "quality": "maj"},
      {"start": 10, "end": 12, "label": "C", "root": 0, "quality": "maj"},
      {"start": 12, "end": 14, "label": "G", "root": 7, "quality": "maj"},
      {"start": 14, "end": 22, "label": "Am", "root": 9, "quality": "min"},
      {"start": 22, "end": 30, "label": "F", "root": 5, "quality": "maj"},
      {"start": 30, "end": 34, "label": "C", "root": 0, "quality": "maj"}]
out = acordes_sobre_letra(sinc, cs, 34.0)
tipos = [l["tipo"] for l in out]
check("intro antes de la primera linea", tipos[:2], ["seccion", "acordes"])
par1 = next(l for l in out if l["tipo"] == "par")
check("acordes sobre la primera linea", [a["t"] for a in par1["ac"]], ["C", "G"])
check("el cambio cae a mitad de la linea", 0 < par1["ac"][1]["col"] < len(par1["txt"]), True)
check("el acorde arranca en palabra", par1["txt"][par1["ac"][1]["col"] - 1], " ")
# El instrumental arrastra el acorde que sigue sonando (Am) y suma el nuevo (F).
check("instrumental en linea propia",
      any(l["tipo"] == "acordes" and "F" in [a["t"] for a in l["ac"]] for l in out), True)

# --- Tiempos para las lineas de la hoja ---
hoja = parse_letra("C        G\nuno dos tres cuatro\nAm\ncinco seis\n\nsin match en ningun lado")
n = tiempos_de_hoja(hoja, sinc)
check("lineas de la hoja con tiempo", n, 2)
check("tiempo de la segunda", [l.get("t0") for l in hoja if l["tipo"] == "par"], [10.0, 14.0])

# --- Una transcripcion con el mismo titulo pero otra letra se reconoce ---
from chordlocal.fuentes import similitud_letra
ref = "todos los dias quando acordo nao tenho mais o tempo que passou mas tenho muito tempo"
misma = parse_letra("C   G\ntodos los dias quando acordo\nAm  F\nnao tenho mais o tempo que passou mas tenho muito tempo")
otra = parse_letra("C   G\nhoy camine por la ciudad buscando\nAm  F\ntus palabras perdidas entre la gente siempre")
check("misma letra", similitud_letra(misma, ref) > 0.8, True)
check("otra letra", similitud_letra(otra, ref) < 0.2, True)
check("hoja sin letra no decide", similitud_letra(parse_letra("C G Am F"), ref), None)

# --- Prior de hoja: favorece su vocabulario, llevado al tono del audio ---
labels = state_labels()
pr = prior_de_hoja([{"r": 4, "q": "maj"}], offset=1)   # hoja en E, audio en Eb
i_eb = next(i for i, c in enumerate(labels) if c and c.root == 3 and c.quality == "maj")
i_gm = next(i for i, c in enumerate(labels) if c and c.root == 7 and c.quality == "min")
check("prior premia Eb y castiga Gm", pr[i_eb] > 0 > pr[i_gm], True)

# --- Armadura con bemoles ---
check("Eb mayor usa bemoles", Key(3, "major").flats, True)
check("C menor usa bemoles", Key(0, "minor").flats, True)
check("E mayor no", Key(4, "major").flats, False)
check("Eb se escribe Eb", Key(3, "major").name(), "Eb mayor")

# --- Beats: compas y fase sobre activaciones sinteticas ---
fps = BT.FPS
dur = 60.0
n_fr = int(dur * fps)
beat_act = np.zeros(n_fr)
down_act = np.zeros(n_fr)
periodo = 0.5                      # 120 BPM
for k, tb in enumerate(np.arange(0.3, dur - 0.5, periodo)):
    i = int(round(tb * fps))
    beat_act[i] = 0.9
    if k % 3 == 1:                 # 3/4 con el primer downbeat en el segundo beat
        down_act[i] = 0.9
BT.activations = lambda y, sr: (beat_act, down_act)
r = BT.track(None, 22050)
check("3/4 detectado", r["metro"], "3/4")
check("tempo 120", abs(r["bpm"] - 120) < 2, True)
check("fase del downbeat", round(float(r["downbeats"][0]), 2), 0.8)
b, d = r["beats"], r["downbeats"]
cnt = {int(np.sum((b >= d[k] - 1e-6) & (b < d[k + 1] - 1e-6))) for k in range(len(d) - 1)}
check("todos los compases de 3", cnt, {3})

# Picos faltantes: el DP tiene que reponer el pulso.
beat_act2 = beat_act.copy()
beat_act2[int(round(10.3 * fps))] = 0
beat_act2[int(round(20.3 * fps))] = 0
BT.activations = lambda y, sr: (beat_act2, down_act)
r2 = BT.track(None, 22050)
check("DP repone beats perdidos", BT.regularidad(r2["beats"]) < 1.1, True)

# --- Borde de pista: fade + silencio + otra cancion a volumen pleno ---
sr = 8000
rng = np.random.default_rng(0)
tema = rng.normal(0, 0.3, sr * 100)
fade = tema[-sr * 6:] * np.logspace(0, -3, sr * 6)     # fade exponencial, -60 dB
y = np.concatenate([tema[:-sr * 6], fade, np.zeros(sr // 2), rng.normal(0, 0.3, sr * 15)])
corte = detect_track_end(y, sr, hop=256)
check("detecta la pista ajena", corte is not None and 96 < corte < 100.6, True)
# Un break a mitad de tema, sin fade, no es un borde de pista.
y2 = np.concatenate([tema[:sr * 80], np.zeros(sr), tema[:sr * 15]])
check("un break no es borde", detect_track_end(y2, sr, hop=256), None)


print(f"\nverificaciones fallidas: {len(fails)}")
for f in fails:
    print("  FALLO:", f)
print("OK" if not fails else "HAY FALLOS")
sys.exit(1 if fails else 0)
