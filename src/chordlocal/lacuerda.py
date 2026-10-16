"""Busqueda de transcripciones en LaCuerda.net.

Automatiza el paso aburrido: encontrar *cual* pagina corresponde a la cancion
que acabas de analizar, entre resultados que son en su mayoria ruido difuso.
Devuelve candidatos ordenados, con su metadata y quien los transcribio, y lee
la transcripcion (acordes sobre letra) de las mejores.

La transcripcion viene en el HTML de la pagina, dentro de
`<div id=t_body><PRE>`, con cada acorde envuelto en `<A>`. (El `<pre
id='tCode'>` vacio que aparece mas arriba NO es la transcripcion: es donde el
JS escribe el codigo interno `ocod`.) Como las etiquetas no ocupan columnas,
al sacarlas el texto conserva la alineacion de acordes sobre la silaba.

El sitio declara que cada transcripcion es para uso privado: se guarda junto
al analisis, en `out/`, que no se publica.

robots.txt de lacuerda.net no prohibe nada pero pide `Crawl-delay: 5`, que se
respeta aca con un throttle global y cache en disco.
"""
from __future__ import annotations

import html as html_mod
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict
from pathlib import Path

BASE = "https://acordes.lacuerda.net"
UA = "ChordLocal/0.2 (analisis musical personal)"
TIMEOUT = 15
CRAWL_DELAY = 5.0          # lo que pide su robots.txt

_last_hit = 0.0


def _fetch(url: str, cache: Path | None = None) -> str:
    """GET con cache en disco y el crawl-delay que pide el sitio."""
    global _last_hit

    if cache is not None:
        key = re.sub(r"[^\w.-]", "_", url.split("://", 1)[-1])[:120]
        cached = cache / f"lacuerda-{key}.html"
        if cached.exists():
            return cached.read_text(encoding="utf-8", errors="replace")

    espera = CRAWL_DELAY - (time.monotonic() - _last_hit)
    if espera > 0:
        time.sleep(espera)

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read().decode("utf-8", "replace")
    _last_hit = time.monotonic()

    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_text(body, encoding="utf-8")
    return body


def _norm(s: str) -> str:
    """Minusculas sin acentos ni puntuacion, para comparar titulos."""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# Articulos y preposiciones: no identifican un tema. Con "las" contando, "Las
# palabras" empataba con cualquier titulo que dijera "palabras".
_VACIAS = {"las", "los", "del", "una", "uno", "con", "por", "que", "the", "and",
           "les", "des", "est", "por", "sin", "mis", "tus", "sus", "nos", "les",
           "das", "dos", "uma", "com", "feat", "remastered", "remaster", "live",
           "version", "official", "video", "audio"}


def _tokens(s: str) -> set[str]:
    return {t for t in _norm(s).split() if len(t) > 2 and t not in _VACIAS}


@dataclass
class Candidato:
    """Una pagina de LaCuerda que podria ser la cancion buscada."""
    artista: str = ""
    tema: str = ""
    url: str = ""
    codigo: str = ""          # ocod, el id interno de la transcripcion
    acordes: str = ""         # los acordes que declara el sitio (notacion latina)
    colaborador: str = ""     # quien la transcribio
    usuario: str = ""
    compositor: str = ""
    album: str = ""
    versiones: list[str] = field(default_factory=list)   # URLs de otras versiones
    puntaje: float = 0.0
    motivos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def buscar_candidatos(consulta: str, cache: Path | None = None,
                      limite: int = 8) -> list[Candidato]:
    """Busca en LaCuerda y devuelve los slugs candidatos, sin abrir cada pagina.

    Los resultados viven en dos arrays JS (`hds` artistas, `fns` temas). Como
    se emparejan cambia de pagina a pagina, y la regla esta en la propia
    funcion `lOpen` del resultado: `hds[NMAX-n]+'/'+fns[n]` en unas,
    `hds[n]+'/'+fns[n]` en otras. Se lee esa regla en vez de suponerla.
    """
    url = (f"{BASE}/busca.php?exp={urllib.parse.quote_plus(consulta)}"
           f"&lang=ES&ini=0")
    html = _fetch(url, cache)
    return candidatos_de_html(html)[:limite]


def candidatos_de_html(html: str) -> list[Candidato]:
    """Pares (artista, tema) de una pagina de resultados de busqueda."""
    hds = re.search(r"var hds=\[(.*?)\];", html, re.S)
    fns = re.search(r"var fns=\[(.*?)\];", html, re.S)
    if not hds or not fns:
        return []
    artistas = re.findall(r"'([^']*)'", hds.group(1))
    temas = re.findall(r"'([^']*)'", fns.group(1))
    n = min(len(artistas), len(temas))
    m = re.search(r"NMAX\s*=\s*(\d+)", html)
    nmax = int(m.group(1)) if m else n - 1

    regla = re.search(r"hds\[([^\]]+)\]\s*\+\s*'/'\s*\+\s*fns\[([^\]]+)\]", html)
    inv_h = bool(regla and "NMAX" in regla.group(1))
    inv_f = bool(regla and "NMAX" in regla.group(2))

    out = []
    for i in range(n):
        ih = nmax - i if inv_h else i
        it = nmax - i if inv_f else i
        if 0 <= ih < len(artistas) and 0 <= it < len(temas):
            a, t = artistas[ih], temas[it]
            out.append(Candidato(artista=a, tema=t, url=f"{BASE}/{a}/{t}.shtml"))
    return out


def leer_pagina(cand: Candidato, cache: Path | None = None) -> Candidato:
    """Completa un candidato con la metadata que el sitio sirve en el HTML.

    LaCuerda deja las variables en un `<script>` al pie: `ocod` (codigo),
    `odes` (acordes declarados), `oclb` (colaborador, como "U:usuario;nombre"),
    `oband` y `orola` con los nombres ya legibles.
    """
    html = _fetch(cand.url, cache)

    def var(nombre: str) -> str:
        m = re.search(rf"{nombre}\s*=\s*'([^']*)'", html)
        return m.group(1).strip() if m else ""

    cand.codigo = var("ocod")
    cand.acordes = var("odes")
    cand.artista = var("oband") or cand.artista
    cand.tema = var("orola") or cand.tema

    clb = var("oclb")
    if clb:
        # "U:usuario;Nombre visible" — a veces solo una de las dos partes.
        partes = clb.split(";")
        cand.usuario = partes[0].removeprefix("U:").strip()
        cand.colaborador = (partes[1] if len(partes) > 1 else partes[0]).strip()

    # Otras versiones de la misma cancion: `trcal.push(['R',0,0,2])`, donde la
    # letra es el tipo (R acordes, T tab, B bajo, K piano, H armonica, D bateria)
    # y el ultimo numero la version: la 1 es la URL base y la n es "-n.shtml".
    base = cand.url.removesuffix(".shtml")
    base = re.sub(r"-\d+$", "", base)
    # Solo las versiones de acordes ('R'); las demas son tab, bajo, piano...
    nums = sorted({int(n) for t, n in
                   re.findall(r"trcal\.push\(\['(\w)'[^\]]*?,(\d+)\]\)", html)
                   if t == "R"})
    cand.versiones = [base + ".shtml" if n == 1 else f"{base}-{n}.shtml"
                      for n in nums if n >= 1]

    m = re.search(r"<div class='tCompo'></div>(.*?)<br>", html, re.S)
    if m:
        cand.compositor = _texto(m.group(1))
    m = re.search(r"<div class='tAlbum'></div>(.*?)</div>", html, re.S)
    if m:
        cand.album = _texto(m.group(1))

    return cand


def leer_transcripcion(url: str, cache: Path | None = None) -> str:
    """Devuelve la transcripcion de una pagina como texto acordes-sobre-letra.

    Cadena vacia si la pagina no trae el bloque (tablatura pura, pagina rota).
    """
    html = _fetch(url, cache)
    return transcripcion_de_html(html)


def transcripcion_de_html(html: str) -> str:
    """Extrae el `<PRE>` de `t_body` y lo pasa a texto plano alineado."""
    m = re.search(r"<div id=['\"]?t_body['\"]?[^>]*>\s*<PRE[^>]*>(.*?)</PRE>",
                  html, re.S | re.I)
    if not m:
        return ""
    cuerpo = m.group(1)
    cuerpo = re.sub(r"<div></div>", "", cuerpo, flags=re.I)
    cuerpo = re.sub(r"<br\s*/?>", "\n", cuerpo, flags=re.I)
    cuerpo = re.sub(r"<[^>]+>", "", cuerpo)
    cuerpo = html_mod.unescape(cuerpo).replace("\r", "")
    return cuerpo.strip("\n")


def _texto(fragmento: str) -> str:
    """Pasa un trozo de HTML a texto plano legible.

    El sitio intercala el link "corregir" dentro de los campos de metadata, y
    separa con &nbsp;, asi que las dos cosas se limpian aca.
    """
    t = re.sub(r"<[^>]+>", " ", fragmento)
    t = html_mod.unescape(t)
    t = re.sub(r"\s*\bcorregir\b\s*$", "", t.strip(), flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def rankear(cands: list[Candidato], titulo: str, artista: str = "") -> list[Candidato]:
    """Ordena los candidatos por parecido con el titulo y artista buscados.

    La busqueda del sitio es muy laxa: para "juan wauters" devuelve "Juan y
    Juan". Sin este filtro, el primer resultado casi nunca es el correcto.
    """
    tt, ta = _tokens(titulo), _tokens(artista)
    for c in cands:
        ct, ca = _tokens(c.tema), _tokens(c.artista)
        c.motivos = []
        puntaje = 0.0

        if tt:
            comunes = tt & ct
            cobertura = len(comunes) / len(tt)
            puntaje += 3.0 * cobertura
            if cobertura == 1.0:
                c.motivos.append("titulo completo")
            elif comunes:
                c.motivos.append(f"titulo {len(comunes)}/{len(tt)}")
        if _norm(c.tema) == _norm(titulo):
            puntaje += 2.0
            c.motivos.append("titulo exacto")
        if ta and (ta & ca):
            puntaje += 2.0 * len(ta & ca) / len(ta)
            c.motivos.append("artista coincide")
        # Penaliza los temas de relleno tipo "musica_de_tv/..." sin relacion.
        if ct - tt and not (tt & ct):
            puntaje -= 0.5

        c.puntaje = round(puntaje, 2)
    return sorted(cands, key=lambda c: -c.puntaje)


def limpiar_titulo(raw: str) -> tuple[str, str]:
    """Saca del titulo de YouTube todo lo que no es el nombre del tema.

    Los titulos vienen como "Artista - Tema (Banda original) (Live on Canal)".
    El buscador de LaCuerda no tolera consultas largas: con el titulo entero
    devuelve cero resultados, asi que hay que dejar solo el tema.
    """
    t = raw.strip()
    artista = ""
    if " - " in t:
        artista, _, t = t.partition(" - ")
    # Todo parentesis y corchete es ruido: version, vivo, canal, remaster.
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    t = re.sub(r"\s*\|.*$", "", t)
    t = re.sub(r"\b(live|en vivo|official|video|audio|lyrics?|cover|"
               r"remaster(ed)?|hd|4k)\b.*$", "", t, flags=re.I)
    artista = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", artista)
    artista = re.sub(r"\s*(ft\.?|feat\.?|&|,)\s.*$", "", artista, flags=re.I)
    return re.sub(r"\s+", " ", t).strip(" -–—\"'“”,"), artista.strip()


def _escalera(titulo: str, artista: str, otros: list[str] | None = None) -> list[str]:
    """Consultas de mas especifica a mas laxa.

    LaCuerda devuelve cero resultados en cuanto la consulta tiene una palabra
    de mas, asi que se prueban varias y se corta en la primera que trae algo.
    """
    pal = _norm(titulo).split()
    qs: list[str] = []
    if artista:
        qs.append(f"{artista} {titulo}")
    qs.append(titulo)
    # Las primeras palabras suelen ser el gancho del titulo.
    for n in (4, 3, 2):
        if len(pal) > n:
            qs.append(" ".join(pal[:n]))
    for o in otros or []:
        qs.append(f"{o} {titulo}")
    if artista:
        qs.append(artista)
    # Sin duplicados, preservando el orden.
    return list(dict.fromkeys(q for q in qs if q.strip()))


def buscar(titulo: str, artista: str = "", cache: Path | None = None,
           detalle: int = 3, otros: list[str] | None = None) -> list[Candidato]:
    """Busqueda completa: consulta, rankea y abre las mejores paginas.

    Se abre solo el top `detalle` para no castigar al sitio: cada apertura
    cuesta 5 segundos de crawl-delay.
    """
    titulo, art_tit = limpiar_titulo(titulo)
    artista = artista or art_tit

    vistos: dict[str, Candidato] = {}
    todos = " ".join([artista] + list(otros or []))
    for q in _escalera(titulo, artista, otros):
        for c in buscar_candidatos(q, cache, limite=60):
            vistos.setdefault(c.url, c)
        # Con un titulo exacto de un artista conocido ya no hace falta seguir
        # aflojando la consulta (cada consulta cuesta 5 s de crawl-delay).
        ranked = rankear(list(vistos.values()), titulo, todos)
        if ranked and ranked[0].puntaje >= 6.5:
            break

    ranked = rankear(list(vistos.values()), titulo, todos)
    for c in ranked[:detalle]:
        try:
            leer_pagina(c, cache)
        except Exception:
            pass          # best-effort: sin metadata el candidato igual sirve
    return ranked


# --- Notacion latina -> cifrado americano -------------------------------------
# LaCuerda escribe los acordes en notacion latina (MI, LA7, DO#m). El resto del
# proyecto trabaja en cifrado americano, asi que se traduce al entrar.

LATINO = {"DO": "C", "RE": "D", "MI": "E", "FA": "F", "SOL": "G",
          "LA": "A", "SI": "B"}
_LATINO_RE = re.compile(r"^(DO|RE|MI|FA|SOL|LA|SI)([#b]?)(.*)$", re.I)


def a_americano(acorde: str) -> str:
    """Traduce un acorde latino a cifrado americano. Si ya lo es, lo deja igual.

    En el campo `odes` el sitio escribe el sostenido como "@" (F@ = F#).
    """
    acorde = acorde.strip().replace("@", "#")
    m = _LATINO_RE.match(acorde.strip())
    if not m:
        return acorde.strip()
    raiz, alt, resto = m.groups()
    bajo = ""
    if "/" in resto:
        resto, _, b = resto.partition("/")
        bajo = "/" + a_americano(b)
    return LATINO[raiz.upper()] + alt + resto + bajo


def resumen_acordes(cand: Candidato) -> list[str]:
    """Los acordes que el sitio declara para la transcripcion, en americano."""
    return [a_americano(t) for t in cand.acordes.split() if t]


def guardar(cands: list[Candidato], destino: Path) -> Path:
    """Deja los candidatos en JSON junto al analisis, para no re-buscar."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps([c.to_dict() for c in cands],
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    return destino
