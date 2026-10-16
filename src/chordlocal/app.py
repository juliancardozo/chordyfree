"""Interfaz grafica local de ChordLocal: un servidor web minimo.

Pegas una URL de YouTube (o una ruta de audio), y la app analiza el audio,
busca letra y acordes online, contrasta todo y abre `cancion.html`: letra con
acordes, reproductor sincronizado y analisis. Corre solo en localhost.
"""
from __future__ import annotations

import json
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
OUT = ROOT / "out"

app = Flask(__name__)

# Trabajos en curso. El analisis tarda ~1 min, asi que corre en un hilo y la
# pagina consulta el progreso; si no, el navegador agotaria el tiempo de espera.
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


def _set(job_id: str, **kw) -> None:
    with LOCK:
        JOBS.setdefault(job_id, {}).update(kw)


def _log(job_id: str, linea: str) -> None:
    with LOCK:
        JOBS[job_id].setdefault("log", []).append(str(linea).rstrip())


def _dir(slug: str) -> Path:
    """Carpeta de una cancion, sin dejar que el slug se escape de out/."""
    d = (OUT / slug).resolve()
    if d.parent != OUT.resolve() or not (d / "analysis.json").is_file():
        abort(404)
    return d


def _nuevo_job(**kw) -> str:
    job_id = uuid.uuid4().hex[:12]
    _set(job_id, estado="en cola", log=[], creado=datetime.now().isoformat(), **kw)
    return job_id


def _biblioteca() -> list[dict]:
    """Canciones ya analizadas, de la mas reciente a la mas vieja."""
    items = []
    if not OUT.exists():
        return items
    for d in OUT.iterdir():
        aj = d / "analysis.json"
        if not aj.is_file():
            continue
        try:
            a = json.loads(aj.read_text())
        except Exception:
            continue
        src = a.get("source", {})
        arm = a.get("armonia", {})
        f = {}
        if (d / "fuentes.json").is_file():
            try:
                f = json.loads((d / "fuentes.json").read_text())
            except Exception:
                pass
        items.append({
            "slug": d.name,
            "titulo": src.get("track") or src.get("title", d.name),
            "artista": src.get("artist", ""),
            "tonalidad": arm.get("tonalidad", ""),
            "bpm": (a.get("tempo") or {}).get("bpm"),
            "compas": (a.get("tempo") or {}).get("meter", ""),
            "duracion": round(src.get("duration", 0)),
            "letra": (f.get("letra") or {}).get("estado") == "ok",
            "acordes_online": bool((f.get("lacuerda") or {}).get("elegido"))
                              or (d / "acordes-importados.txt").is_file(),
            "version": a.get("version", 1),
            "mtime": aj.stat().st_mtime,
        })
    items.sort(key=lambda x: -x["mtime"])
    return items


def _correr(job_id: str, target: str, con_notas: bool, con_web: bool,
            buscar: bool) -> None:
    """Pipeline completo en segundo plano: audio -> analisis -> busqueda -> hoja."""
    from . import fuentes, salidas
    from .analyze import analyze
    from .fetch import resolve

    def progreso(linea: str) -> None:
        _log(job_id, linea)

    try:
        _set(job_id, estado="obteniendo audio")
        progreso("obteniendo audio...")
        src = resolve(target, CACHE)
        _set(job_id, titulo=src.title, artista=src.artist)
        progreso(f"  {src.title} ({src.duration:.0f} s)")

        _set(job_id, estado="analizando")
        a = analyze(src, with_notes=con_notas, with_web=con_web, progress=progreso)

        slug = src.video_id or "track"
        outdir = OUT / slug
        outdir.mkdir(parents=True, exist_ok=True)

        if buscar and con_web:
            _set(job_id, estado="buscando letra y acordes")
            try:
                fuentes.buscar(a, outdir, CACHE, progress=progreso)
            except Exception as e:
                progreso(f"aviso: la busqueda online fallo: {e}")

        progreso("escribiendo salidas...")
        salidas.escribir(a, outdir, want_notes=con_notas, aviso=progreso)
        _set(job_id, estado="listo", slug=slug)
        progreso("listo")
    except Exception as e:
        _set(job_id, estado="error", error=str(e),
             traza=traceback.format_exc()[-1500:])


def _rebuscar(job_id: str, slug: str, titulo: str, artista: str) -> None:
    from . import fuentes, pagina
    from .analyze import Analysis
    try:
        d = OUT / slug
        a = Analysis.load(d / "analysis.json")
        _set(job_id, estado="buscando")
        fuentes.buscar(a, d, CACHE, consulta=titulo, artista=artista,
                       progress=lambda m: _log(job_id, m.strip()))
        pagina.build(a, d)
        _set(job_id, estado="listo", slug=slug)
    except Exception as e:
        _set(job_id, estado="error", error=str(e), traza=traceback.format_exc()[-1500:])


def _regenerar(slug: str) -> Path:
    from . import pagina
    from .analyze import Analysis
    d = OUT / slug
    return pagina.build(Analysis.load(d / "analysis.json"), d)


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

@app.get("/")
def index():
    return PAGINA


@app.get("/api/biblioteca")
def api_biblioteca():
    return jsonify(_biblioteca())


@app.post("/api/analizar")
def api_analizar():
    data = request.get_json(force=True, silent=True) or {}
    target = (data.get("target") or "").strip()
    if not target:
        return jsonify({"error": "falta la URL o la ruta del archivo"}), 400
    job_id = _nuevo_job()
    threading.Thread(target=_correr, daemon=True, args=(
        job_id, target, bool(data.get("notas", True)), bool(data.get("web", True)),
        bool(data.get("buscar", True)))).start()
    return jsonify({"job": job_id})


@app.get("/api/estado/<job_id>")
def api_estado(job_id: str):
    with LOCK:
        j = dict(JOBS.get(job_id) or {})
    if not j:
        return jsonify({"error": "trabajo desconocido"}), 404
    return jsonify(j)


@app.post("/api/fuentes/<slug>")
def api_fuentes(slug: str):
    _dir(slug)
    data = request.get_json(force=True, silent=True) or {}
    job_id = _nuevo_job(slug=slug)
    threading.Thread(target=_rebuscar, daemon=True, args=(
        job_id, slug, (data.get("titulo") or "").strip(),
        (data.get("artista") or "").strip())).start()
    return jsonify({"job": job_id})


@app.post("/api/usar/<slug>")
def api_usar(slug: str):
    """Elige otra transcripcion de LaCuerda de las candidatas ya leidas."""
    from . import fuentes, lacuerda
    d = _dir(slug)
    url = (request.get_json(force=True, silent=True) or {}).get("url", "")
    f = fuentes.cargar(d)
    cands = (f.get("lacuerda") or {}).get("candidatos", [])
    elegido = next((c for c in cands if c.get("url") == url), None)
    if not elegido or not url.startswith(lacuerda.BASE):
        return jsonify({"error": "candidato desconocido"}), 400
    try:
        texto = lacuerda.leer_transcripcion(url, CACHE)
    except Exception as e:
        return jsonify({"error": str(e)}), 502
    (d / "acordes-online.txt").write_text(texto, encoding="utf-8")
    f["lacuerda"]["elegido"] = elegido
    fuentes.guardar(f, d)
    # Elegir explicitamente una candidata descarta una importacion manual previa.
    for n in ("acordes-importados.txt", "acordes-importados.json"):
        (d / n).unlink(missing_ok=True)
    _regenerar(slug)
    return jsonify({"ok": True})


@app.post("/api/importar/<slug>")
def api_importar(slug: str):
    from . import fuentes
    d = _dir(slug)
    data = request.get_json(force=True, silent=True) or {}
    if data.get("borrar"):
        for n in ("acordes-importados.txt", "acordes-importados.json"):
            (d / n).unlink(missing_ok=True)
    else:
        texto = data.get("texto") or ""
        if not texto.strip():
            return jsonify({"error": "texto vacio"}), 400
        if len(texto) > 200_000:
            return jsonify({"error": "texto demasiado largo"}), 400
        fuentes.importar(d, texto, url=(data.get("url") or "")[:500])
    _regenerar(slug)
    return jsonify({"ok": True})


@app.get("/cancion/<slug>")
def cancion(slug: str):
    d = _dir(slug)
    # Analisis hechos con versiones anteriores no tienen cancion.html: se arma
    # al vuelo, sin re-analizar.
    if not (d / "cancion.html").is_file():
        _regenerar(slug)
    return send_from_directory(d, "cancion.html")


@app.post("/api/regenerar/<slug>")
def api_regenerar(slug: str):
    _dir(slug)
    _regenerar(slug)
    return jsonify({"ok": True})


@app.get("/hoja/<slug>")
def hoja(slug: str):
    d = _dir(slug)
    if not (d / "hoja.html").is_file():
        abort(404)
    return send_from_directory(d, "hoja.html")


@app.get("/audio/<slug>")
def audio(slug: str):
    d = _dir(slug)
    a = json.loads((d / "analysis.json").read_text())
    wav = Path((a.get("source") or {}).get("wav", "")).resolve()
    # Solo se sirve audio de la cache del proyecto.
    if wav.parent != CACHE.resolve() or not wav.is_file():
        abort(404)
    return send_file(wav, mimetype="audio/wav", conditional=True)


@app.get("/archivo/<slug>/<path:nombre>")
def archivo(slug: str, nombre: str):
    d = _dir(slug)
    p = (d / nombre).resolve()
    if p.parent != d or not p.is_file():
        abort(404)
    return send_from_directory(d, nombre, as_attachment=True)


@app.get("/importar")
def importar():
    return IMPORTAR


def run(host: str = "127.0.0.1", port: int = 7700, abrir: bool = True) -> None:
    if abrir:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"\n  ChordLocal corriendo en http://{host}:{port}")
    print("  Ctrl+C para detener\n")
    app.run(host=host, port=port, debug=False, threaded=True)


_ESTILO = r"""
:root{--bg:#fbfaf7;--fg:#16150f;--mut:#6b6658;--line:#e2ddd0;--card:#fff;
  --acc:#9a5b2d;--ok:#2f6b46;--err:#a33;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#13120f;--fg:#eceadf;--mut:#9a948a;
  --line:#2b2924;--card:#1b1a16;--acc:#e0a368;--ok:#6cc08c;--err:#e07b7b;color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 ui-sans-serif,system-ui,-apple-system,sans-serif}
a{color:var(--acc)}
.wrap{max-width:880px;margin:0 auto;padding:30px 18px 70px}
h1{font-size:1.6rem;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--mut);font-size:.92rem;margin-bottom:24px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:18px;margin-bottom:24px}
label{display:block;font-size:.8rem;color:var(--mut);margin-bottom:5px}
input[type=text],select,textarea{width:100%;font:inherit;padding:10px 12px;border-radius:8px;
  border:1px solid var(--line);background:var(--bg);color:var(--fg)}
textarea{font:13px/1.4 ui-monospace,Menlo,monospace;min-height:260px}
input[type=text]:focus{outline:2px solid var(--acc);outline-offset:-1px}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:12px}
button{font:inherit;font-size:.9rem;background:var(--acc);color:#fff;border:0;
  border-radius:8px;padding:9px 18px;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
.chk{display:flex;align-items:center;gap:6px;font-size:.85rem;color:var(--mut)}
.muted{color:var(--mut)} .err{color:var(--err);font-size:.85rem;margin-top:8px}
.ok{color:var(--ok)}
"""

PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChordLocal</title>
<style>""" + _ESTILO + r"""
#prog{display:none;margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
#prog.on{display:block}
.pasos{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.78rem;
  color:var(--mut);white-space:pre-wrap;max-height:220px;overflow:auto;
  background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;margin-top:8px}
.spin{display:inline-block;width:11px;height:11px;border:2px solid var(--line);
  border-top-color:var(--acc);border-radius:50%;animation:s .8s linear infinite;
  vertical-align:-1px;margin-right:7px}
@keyframes s{to{transform:rotate(360deg)}}
h2{font-size:1rem;margin:0 0 12px;display:flex;align-items:center;gap:9px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
.song{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:12px 15px;margin-bottom:9px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.song .t{font-weight:600;flex:1;min-width:200px}
.song .t a{color:var(--fg);text-decoration:none}
.song .t a:hover{color:var(--acc)}
.song .t small{display:block;font-weight:400;color:var(--mut);font-size:.8rem}
.tag{font-size:.74rem;color:var(--mut);background:var(--bg);
  border:1px solid var(--line);border-radius:6px;padding:2px 8px;white-space:nowrap}
.tag.si{color:var(--ok);border-color:var(--ok)}
.acts{display:flex;gap:6px;flex-wrap:wrap}
.acts a{font-size:.8rem;text-decoration:none;color:var(--acc);
  border:1px solid var(--line);border-radius:6px;padding:4px 9px}
.acts a.main{background:var(--acc);color:#fff;border-color:var(--acc)}
.vacio{color:var(--mut);font-size:.9rem;padding:14px 0}
details{margin-top:24px;font-size:.88rem}
summary{cursor:pointer;color:var(--mut)}
code{font-size:.8rem;word-break:break-all}
</style></head><body><div class="wrap">

<h1>ChordLocal</h1>
<div class="sub">Pegá un video y obtené la letra con acordes, sincronizada con el audio, y un análisis de la canción.</div>

<div class="panel">
  <label for="t">URL de YouTube o ruta de un archivo de audio</label>
  <input id="t" type="text" placeholder="https://www.youtube.com/watch?v=...  o  /Users/vos/Music/tema.mp3" autocomplete="off">
  <div class="row">
    <button id="go">Analizar</button>
    <span class="chk"><input type="checkbox" id="buscar" checked> buscar letra y acordes online</span>
    <span class="chk"><input type="checkbox" id="notas"> tablatura (más lento)</span>
    <span class="chk"><input type="checkbox" id="web" checked> usar internet</span>
  </div>
  <div id="prog">
    <div id="estado"></div>
    <div class="pasos" id="pasos"></div>
    <div class="err" id="err"></div>
  </div>
</div>

<h2>Biblioteca</h2>
<div id="lista"><div class="vacio">cargando...</div></div>

<details>
  <summary>Traer acordes desde tu navegador (Cifra Club, Ultimate Guitar…)</summary>
  <p>Algunos sitios, como Cifra Club, bloquean a los programas automáticos. Para esos,
  arrastrá este marcador a la barra de favoritos: <a id="bm" href="#">Enviar a ChordLocal</a>.
  Abrí la cifra en tu navegador, tocá el marcador, y elegí a qué canción de la biblioteca va.
  Si seleccionás un fragmento antes, se envía solo eso.</p>
</details>

<script>
const $=i=>document.getElementById(i);
let timer=null;
const esc=s=>(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

$("bm").href="javascript:(()=>{let t=String(getSelection()).trim();if(!t){let b=null;document.querySelectorAll('pre').forEach(p=>{if(!b||p.innerText.length>b.innerText.length)b=p});t=b?b.innerText:''}if(!t){alert('No encontre una cifra en esta pagina: selecciona el texto y volve a tocar el marcador.');return}window.open('"+location.origin+"/importar#'+encodeURIComponent(JSON.stringify({t:t,u:location.href,ti:document.title})))})()";
$("bm").onclick=e=>{e.preventDefault();alert("Arrastralo a la barra de favoritos; se usa desde la página de la cifra.");};

async function cargar(){
  const r=await fetch("/api/biblioteca"); const xs=await r.json();
  if(!xs.length){$("lista").innerHTML='<div class="vacio">Todavía no analizaste ninguna canción.</div>';return;}
  $("lista").innerHTML=xs.map(s=>{
    const tags=[s.tonalidad,s.bpm?Math.round(s.bpm)+" BPM":"",s.compas,
      s.duracion?Math.floor(s.duracion/60)+":"+String(s.duracion%60).padStart(2,"0"):""]
      .filter(Boolean).map(t=>'<span class="tag">'+esc(t)+'</span>').join("")+
      (s.letra?'<span class="tag si">letra</span>':'')+(s.acordes_online?'<span class="tag si">acordes online</span>':'')+
      (s.version<2?'<span class="tag" title="Analizado con una versión anterior: volvé a analizarlo para usar el detector nuevo">análisis viejo</span>':'');
    return '<div class="song"><div class="t"><a href="/cancion/'+s.slug+'" target="_blank">'+esc(s.titulo)+'</a>'+
      (s.artista?'<small>'+esc(s.artista)+'</small>':'')+'</div>'+tags+
      '<div class="acts"><a class="main" href="/cancion/'+s.slug+'" target="_blank">Abrir</a>'+
      '<a href="/hoja/'+s.slug+'" target="_blank">Hoja analítica</a>'+
      '<a href="/archivo/'+s.slug+'/cancion.mid">MIDI</a>'+
      '<a href="/archivo/'+s.slug+'/partitura.musicxml">MusicXML</a></div></div>';
  }).join("");
}

$("go").onclick=async()=>{
  const target=$("t").value.trim(); if(!target) return;
  $("go").disabled=true; $("prog").className="on";
  $("err").textContent=""; $("pasos").textContent="";
  $("estado").innerHTML='<span class="spin"></span>enviando...';
  const r=await fetch("/api/analizar",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({target,notas:$("notas").checked,web:$("web").checked,buscar:$("buscar").checked})});
  const j=await r.json();
  if(j.error){fin(); $("err").textContent=j.error; return;}
  timer=setInterval(()=>poll(j.job),1200); poll(j.job);
};
$("t").addEventListener("keydown",e=>{if(e.key==="Enter") $("go").click();});

async function poll(job){
  const r=await fetch("/api/estado/"+job); const j=await r.json();
  $("pasos").textContent=(j.log||[]).join("\n");
  $("pasos").scrollTop=$("pasos").scrollHeight;
  if(j.estado==="listo"){
    clearInterval(timer); fin();
    $("estado").innerHTML='<span class="ok">Listo.</span> <a href="/cancion/'+j.slug+'" target="_blank">Abrir la canción</a>';
    cargar(); window.open("/cancion/"+j.slug,"_blank");
  }else if(j.estado==="error"){
    clearInterval(timer); fin();
    $("estado").textContent="Falló el análisis.";
    $("err").textContent=j.error||"error desconocido";
  }else{
    $("estado").innerHTML='<span class="spin"></span>'+esc(j.estado)+(j.titulo?' — '+esc(j.titulo):'');
  }
}
function fin(){$("go").disabled=false;}
cargar();
</script></div></body></html>
"""

IMPORTAR = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Importar acordes · ChordLocal</title>
<style>""" + _ESTILO + r"""</style></head><body><div class="wrap">
<h1>Importar acordes</h1>
<div class="sub" id="desde"></div>
<div class="panel">
  <label for="slug">¿A qué canción de la biblioteca va?</label>
  <select id="slug"></select>
  <label for="txt" style="margin-top:14px">Transcripción (podés editarla antes de guardar)</label>
  <textarea id="txt"></textarea>
  <div class="row"><button id="ok">Guardar y abrir</button><span class="muted" id="est"></span></div>
</div>
<script>
const $=i=>document.getElementById(i);
let D={t:"",u:"",ti:""};
try{D=JSON.parse(decodeURIComponent(location.hash.slice(1)))}catch(e){}
$("txt").value=D.t||"";
$("desde").textContent=D.u?"Desde "+D.u:"Pegá la transcripción acá.";
const norm=s=>(s||"").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g,"").replace(/[^a-z0-9]+/g," ");
(async()=>{
  const xs=await (await fetch("/api/biblioteca")).json();
  const ti=new Set(norm(D.ti).split(" ").filter(w=>w.length>2));
  const sc=s=>norm(s.titulo+" "+s.artista).split(" ").filter(w=>ti.has(w)).length;
  xs.sort((a,b)=>sc(b)-sc(a));
  $("slug").innerHTML=xs.map(s=>'<option value="'+s.slug+'">'+(s.titulo+(s.artista?" — "+s.artista:"")).replace(/</g,"&lt;")+'</option>').join("");
})();
$("ok").onclick=async()=>{
  $("ok").disabled=true;$("est").textContent="guardando...";
  const slug=$("slug").value;
  const r=await fetch("/api/importar/"+slug,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({texto:$("txt").value,url:D.u})});
  const j=await r.json();
  if(j.ok) location.href="/cancion/"+slug; else {$("est").textContent=j.error||"error";$("ok").disabled=false;}
};
</script></div></body></html>
"""
