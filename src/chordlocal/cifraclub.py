"""Cifra Club (cifraclub.com.br): intento directo, con salida manual.

Cifra Club esta detras de Akamai y responde 403 a cualquier cliente que no sea
un navegador, aunque se identifique honestamente. Esta herramienta no se
disfraza de navegador para sortear ese bloqueo: es una decision del sitio.

Asi que el modulo hace tres cosas:
  1. arma la URL probable de la cifra y la del buscador, para abrirlas a mano;
  2. intenta bajarla con el User-Agent propio, por si el sitio la sirve;
  3. si el bloqueo sigue, la cifra entra por la pagina de importacion de la app
     (pegando el texto, o con el marcador "Enviar a ChordLocal" desde tu propio
     navegador, que es exactamente copiar y pegar automatizado).

La cifra de Cifra Club viene en un `<pre>` con los acordes en `<b>`; el mismo
parser sirve para el HTML que se importe a mano.
"""
from __future__ import annotations

import html as html_mod
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.cifraclub.com.br"
UA = "ChordLocal/0.2 (analisis musical personal)"
TIMEOUT = 15


class Bloqueado(RuntimeError):
    """El sitio rechazo al cliente automatizado (403)."""


def slug(s: str) -> str:
    """'Legião Urbana' -> 'legiao-urbana', como arma Cifra Club sus rutas."""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def url_probable(titulo: str, artista: str) -> str:
    if not artista or not titulo:
        return ""
    return f"{BASE}/{slug(artista)}/{slug(titulo)}/"


def url_busqueda(titulo: str, artista: str = "") -> str:
    return f"{BASE}/?q=" + urllib.parse.quote_plus(f"{artista} {titulo}".strip())


def cifra_de_html(html: str) -> str:
    """Extrae la cifra (acordes sobre letra) del HTML de una pagina de Cifra Club."""
    m = re.search(r"<pre[^>]*>(.*?)</pre>", html, re.S | re.I)
    if not m:
        return ""
    t = m.group(1)
    # Las tablaturas vienen en <span class="tablatura">: se conservan como texto.
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    return html_mod.unescape(t).replace("\r", "").strip("\n")


def leer(url: str, cache: Path | None = None) -> str:
    """Intenta bajar la cifra. Lanza `Bloqueado` si el sitio la niega."""
    if cache is not None:
        key = re.sub(r"[^\w.-]", "_", url.split("://", 1)[-1])[:120]
        cached = cache / f"cifraclub-{key}.html"
        if cached.exists():
            return cifra_de_html(cached.read_text(encoding="utf-8", errors="replace"))

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise Bloqueado(f"Cifra Club rechaza clientes automatizados (HTTP {e.code})")
        if e.code == 404:
            return ""
        raise

    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_text(body, encoding="utf-8")
    return cifra_de_html(body)
