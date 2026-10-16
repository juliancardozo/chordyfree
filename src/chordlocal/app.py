"""Interfaz grafica local de ChordLocal: un servidor web minimo.

Reusa la hoja HTML que ya genera el pipeline y agrega lo que faltaba: lanzar
analisis y navegar la biblioteca sin pasar por la terminal. Corre solo en
localhost; no expone nada hacia afuera.
"""
from __future__ import annotations

import json
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

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
        items.append({
            "slug": d.name,
            "titulo": src.get("title", d.name),
            "artista": src.get("artist", ""),
            "tonalidad": arm.get("tonalidad", ""),
            "bpm": (a.get("tempo") or {}).get("bpm"),
            "compas": (a.get("tempo") or {}).get("meter", ""),
            "duracion": round(src.get("duration", 0)),
            "acordes": len([c for c in a.get("chords", []) if c.get("root") is not None]),
            "hoja": (d / "hoja.html").is_file(),
            "mtime": aj.stat().st_mtime,
        })
    items.sort(key=lambda x: -x["mtime"])
    return items


def _correr(job_id: str, target: str, con_notas: bool, con_web: bool) -> None:
    """Ejecuta el pipeline completo en segundo plano, reportando progreso."""
    from . import render, sheet
    from .analyze import analyze
    from .fetch import resolve

    def progreso(linea: str) -> None:
        with LOCK:
            JOBS[job_id].setdefault("log", []).append(str(linea).strip())

    try:
        _set(job_id, estado="obteniendo", log=["obteniendo audio..."])
        src = resolve(target, CACHE)
        _set(job_id, titulo=src.title, artista=src.artist)

        _set(job_id, estado="analizando")
        a = analyze(src, with_notes=con_notas, with_web=con_web, progress=progreso)

        slug = src.video_id or "track"
        outdir = OUT / slug
        outdir.mkdir(parents=True, exist_ok=True)

        progreso("escribiendo salidas...")
        (outdir / "analysis.json").write_text(a.to_json())
        (outdir / "acordes.txt").write_text(
            render.summary(a) + "\n\n" + render.chord_grid(a) + "\n"
            + render.chord_shapes(a))
        (outdir / "cancion.chordpro").write_text(render.chordpro(a))
        if a.tab:
            (outdir / "tablatura.txt").write_text(render.ascii_tab(a))
        for fn, nombre in ((lambda: render.musicxml(a, outdir / "partitura.musicxml"),
                            "MusicXML"),
                           (lambda: render.midi(a, outdir / "cancion.mid"), "MIDI")):
            try:
                fn()
            except Exception as e:
                progreso(f"aviso: no se pudo generar {nombre}: {e}")
        sheet.build(a, outdir / "hoja.html")

        _set(job_id, estado="listo", slug=slug)
        progreso("listo")
    except Exception as e:
        _set(job_id, estado="error", error=str(e),
             traza=traceback.format_exc()[-1200:])


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

    job_id = uuid.uuid4().hex[:12]
    _set(job_id, estado="en cola", log=[], creado=datetime.now().isoformat())
    t = threading.Thread(target=_correr, daemon=True, args=(
        job_id, target, bool(data.get("notas", True)), bool(data.get("web", True))))
    t.start()
    return jsonify({"job": job_id})


@app.get("/api/estado/<job_id>")
def api_estado(job_id: str):
    with LOCK:
        j = JOBS.get(job_id)
    if not j:
        return jsonify({"error": "trabajo desconocido"}), 404
    return jsonify(j)


@app.get("/hoja/<slug>")
def hoja(slug: str):
    d = OUT / slug
    if not (d / "hoja.html").is_file():
        abort(404)
    return send_from_directory(d, "hoja.html")


@app.get("/archivo/<slug>/<path:nombre>")
def archivo(slug: str, nombre: str):
    d = (OUT / slug).resolve()
    # Evitar que un nombre con ".." se escape del directorio de salidas.
    if not str(d).startswith(str(OUT.resolve())) or not (d / nombre).is_file():
        abort(404)
    return send_from_directory(d, nombre, as_attachment=True)


def run(host: str = "127.0.0.1", port: int = 7700, abrir: bool = True) -> None:
    if abrir:
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    print(f"\n  ChordLocal corriendo en http://{host}:{port}")
    print("  Ctrl+C para detener\n")
    app.run(host=host, port=port, debug=False, threaded=True)


PAGINA = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChordLocal</title>
<style>
:root{--bg:#fbfaf7;--fg:#16150f;--mut:#6b6658;--line:#e2ddd0;--card:#fff;
  --acc:#9a5b2d;--ok:#2f6b46;--err:#a33;}
@media (prefers-color-scheme:dark){:root{--bg:#13120f;--fg:#eceadf;--mut:#9a948a;
  --line:#2b2924;--card:#1b1a16;--acc:#d99a5f;--ok:#6cc08c;--err:#e07b7b;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 ui-sans-serif,system-ui,-apple-system,sans-serif}
.wrap{max-width:860px;margin:0 auto;padding:30px 20px 70px}
h1{font-size:1.6rem;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--mut);font-size:.92rem;margin-bottom:26px}
.panel{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:18px;margin-bottom:26px}
label{display:block;font-size:.8rem;color:var(--mut);margin-bottom:5px}
input[type=text]{width:100%;font:inherit;padding:10px 12px;border-radius:8px;
  border:1px solid var(--line);background:var(--bg);color:var(--fg)}
input[type=text]:focus{outline:2px solid var(--acc);outline-offset:-1px}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-top:12px}
button{font:inherit;font-size:.9rem;background:var(--acc);color:#fff;border:0;
  border-radius:8px;padding:9px 18px;cursor:pointer}
button:disabled{opacity:.5;cursor:default}
button.sec{background:transparent;color:var(--fg);border:1px solid var(--line)}
.chk{display:flex;align-items:center;gap:6px;font-size:.85rem;color:var(--mut)}
#prog{display:none;margin-top:16px;border-top:1px solid var(--line);padding-top:14px}
#prog.on{display:block}
.pasos{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.8rem;
  color:var(--mut);white-space:pre-wrap;max-height:190px;overflow:auto;
  background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px}
.spin{display:inline-block;width:11px;height:11px;border:2px solid var(--line);
  border-top-color:var(--acc);border-radius:50%;animation:s .8s linear infinite;
  vertical-align:-1px;margin-right:7px}
@keyframes s{to{transform:rotate(360deg)}}
h2{font-size:1rem;margin:0 0 12px;display:flex;align-items:center;gap:9px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
.song{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:13px 16px;margin-bottom:9px;display:flex;gap:14px;align-items:center;
  flex-wrap:wrap}
.song .t{font-weight:600;flex:1;min-width:190px}
.song .t small{display:block;font-weight:400;color:var(--mut);font-size:.8rem}
.tag{font-size:.75rem;color:var(--mut);background:var(--bg);
  border:1px solid var(--line);border-radius:6px;padding:2px 8px;white-space:nowrap}
.acts{display:flex;gap:7px}
.acts a{font-size:.8rem;text-decoration:none;color:var(--acc);
  border:1px solid var(--line);border-radius:6px;padding:5px 10px}
.acts a:hover{border-color:var(--acc)}
.vacio{color:var(--mut);font-size:.9rem;padding:14px 0}
.err{color:var(--err);font-size:.85rem;margin-top:8px}
.ok{color:var(--ok)}
</style></head><body><div class="wrap">

<h1>ChordLocal</h1>
<div class="sub">Analisis local de canciones: acordes, tablatura, partitura y armonia.</div>

<div class="panel">
  <label for="t">URL de YouTube o ruta de un archivo de audio</label>
  <input id="t" type="text" placeholder="https://www.youtube.com/watch?v=...  o  /Users/vos/Music/tema.mp3"
         autocomplete="off">
  <div class="row">
    <button id="go">Analizar</button>
    <span class="chk"><input type="checkbox" id="notas" checked> tablatura (mas lento)</span>
    <span class="chk"><input type="checkbox" id="web" checked> contrastar con internet</span>
  </div>
  <div id="prog">
    <div id="estado"></div>
    <div class="pasos" id="pasos"></div>
    <div class="err" id="err"></div>
  </div>
</div>

<h2>Biblioteca</h2>
<div id="lista"><div class="vacio">cargando...</div></div>

<script>
const $=i=>document.getElementById(i);
let timer=null;

async function cargar(){
  const r=await fetch("/api/biblioteca"); const xs=await r.json();
  if(!xs.length){$("lista").innerHTML='<div class="vacio">Todavia no analizaste ninguna cancion.</div>';return;}
  $("lista").innerHTML=xs.map(s=>{
    const tags=[s.tonalidad,s.bpm?s.bpm+" BPM":"",s.compas,
                s.duracion?Math.floor(s.duracion/60)+":"+String(s.duracion%60).padStart(2,"0"):"",
                s.acordes?s.acordes+" acordes":""].filter(Boolean)
      .map(t=>'<span class="tag">'+t+'</span>').join("");
    const hoja=s.hoja?'<a href="/hoja/'+s.slug+'" target="_blank">Abrir hoja</a>':"";
    return '<div class="song"><div class="t">'+esc(s.titulo)+
      (s.artista?'<small>'+esc(s.artista)+'</small>':'')+'</div>'+tags+
      '<div class="acts">'+hoja+
      '<a href="/archivo/'+s.slug+'/acordes.txt">Acordes</a>'+
      '<a href="/archivo/'+s.slug+'/partitura.musicxml">Partitura</a>'+
      '</div></div>';
  }).join("");
}
function esc(s){return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));}

$("go").onclick=async()=>{
  const target=$("t").value.trim(); if(!target) return;
  $("go").disabled=true; $("prog").className="on";
  $("err").textContent=""; $("pasos").textContent="";
  $("estado").innerHTML='<span class="spin"></span>enviando...';
  const r=await fetch("/api/analizar",{method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({target,notas:$("notas").checked,web:$("web").checked})});
  const j=await r.json();
  if(j.error){fin(); $("err").textContent=j.error; return;}
  timer=setInterval(()=>poll(j.job),1200); poll(j.job);
};

async function poll(job){
  const r=await fetch("/api/estado/"+job); const j=await r.json();
  $("pasos").textContent=(j.log||[]).join("\n");
  $("pasos").scrollTop=$("pasos").scrollHeight;
  if(j.estado==="listo"){
    clearInterval(timer); fin();
    $("estado").innerHTML='<span class="ok">Listo.</span> '+
      '<a href="/hoja/'+j.slug+'" target="_blank">Abrir la hoja</a>';
    cargar();
  }else if(j.estado==="error"){
    clearInterval(timer); fin();
    $("estado").textContent="Fallo el analisis.";
    $("err").textContent=j.error||"error desconocido";
  }else{
    $("estado").innerHTML='<span class="spin"></span>'+(j.titulo||j.estado);
  }
}
function fin(){$("go").disabled=false;}
cargar();
</script></div></body></html>
"""
