"""cancion.html: la hoja final — letra con acordes, reproductor sincronizado y analisis.

Junta todo lo que se sabe del tema:

  * acordes y tiempos del analisis del audio (refinados con la transcripcion
    online si la hay y concuerda);
  * la letra con acordes, de la mejor fuente disponible:
      1. transcripcion online o importada (acordes sobre letra), llevada al
         tono de la grabacion y, si hay letra sincronizada, con tiempos;
      2. letra sincronizada de LRCLIB con los acordes del audio encima;
      3. letra sin sincronizar + grilla de acordes por compas;
      4. solo la grilla;
  * el analisis musical en texto (fusion.analisis) y el detalle de fuentes.

Es un archivo autocontenido. Abierto desde la app reproduce el audio local;
abierto como archivo, lo busca en ../../cache/.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import fuentes as fu
from . import fusion
from .analyze import Analysis
from .playalong import _vocabulario, comparar, parse_letra
from .sheet import JS_SHAPES, _svg_diagram_js

UMBRAL_REFINAR = 55.0     # % de concordancia minima para dejar que la hoja guie


def armar(a: Analysis, outdir: Path) -> dict:
    """Calcula todos los datos de la pagina (sin escribir nada)."""
    src = a.source or {}
    f = fu.cargar(outdir)
    fin = float(src.get("fin_musica") or src.get("duration") or 0)

    texto, meta = fu.transcripcion_activa(outdir)
    lineas = parse_letra(texto) if texto.strip() else []
    voc = _vocabulario(lineas)
    comp = comparar(voc, a) if voc else None

    # Con concordancia razonable, la hoja se lleva al tono que suena: asi la
    # hoja, la linea de tiempo y los diagramas comparten una sola referencia y
    # el transporte mueve todo junto. El tono escrito queda a un clic.
    llevada = 0
    if comp and comp.get("offset") and comp.get("puntaje", 0) >= 40:
        llevada = comp["offset"]
        for l in lineas:
            for ac in l.get("ac", []):
                if ac.get("r") is not None:
                    ac["r"] = (ac["r"] - llevada) % 12
                if ac.get("b") is not None:
                    ac["b"] = (ac["b"] - llevada) % 12
        voc = [{"r": (v["r"] - llevada) % 12, "q": v["q"]} for v in voc]

    chords = a.chords
    refinado = False
    if comp and comp.get("puntaje", 0) >= UMBRAL_REFINAR:
        r = fusion.refinar(a, voc, comp["offset"] - llevada)
        if r:
            chords, refinado = r, True

    letra = f.get("letra") or {}
    sinc = letra.get("sincronizada") or []
    modo = "grilla"
    con_tiempo = 0
    if lineas:
        modo = "hoja"
        if sinc:
            con_tiempo = fusion.tiempos_de_hoja(lineas, sinc)
    elif sinc:
        modo = "sincronizada"
        lineas = fusion.acordes_sobre_letra(sinc, chords, fin)
        con_tiempo = sum(1 for l in lineas if "t0" in l)
    elif letra.get("texto"):
        modo = "plana"

    hoja = {"lineas": lineas, "comparacion": comp} if modo == "hoja" else None
    key = fusion.key_de_acordes(chords, a) if refinado else None
    an = fusion.analisis(a, chords, hoja, f, key=key)

    timeline = [{"s": c["start"], "e": c["end"], "r": c["root"], "q": c["quality"],
                 "b": c.get("bar", 0)} for c in chords if c.get("root") is not None]
    vocab_audio = []
    vistos = set()
    for c in timeline:
        k = (c["r"], c["q"])
        if k not in vistos:
            vistos.add(k)
            vocab_audio.append({"r": c["r"], "q": c["q"]})

    wav = Path(src.get("wav", ""))
    lc = (f.get("lacuerda") or {})
    return {
        "slug": outdir.name,
        "titulo": (f.get("consulta") or {}).get("titulo")
                  or fu.titulo_y_artista(a)[0] or src.get("title", "Canción"),
        "titulo_video": src.get("title", ""),
        "artista": (f.get("consulta") or {}).get("artista") or src.get("artist", ""),
        "origen": src.get("origin", ""),
        "tonalidad": an["tonalidad"],
        "flats": an["flats"],
        "key": {"t": an["tonica"], "m": an["modo"]},
        "bpm": (a.tempo or {}).get("bpm") or 0,
        "compas": (a.tempo or {}).get("meter", "4/4"),
        "duracion": fin,
        "audio": {"app": f"/audio/{outdir.name}",
                  "archivo": f"../../cache/{wav.name}" if wav.name else ""},
        "timeline": timeline,
        "refinado": refinado,
        "modo": modo,
        "lineas": lineas,
        "con_tiempo": con_tiempo,
        "letra_plana": letra.get("texto", "") if modo == "plana" else "",
        "vocabulario": voc if modo == "hoja" else vocab_audio,
        "comparacion": comp,
        "llevada": llevada,
        "analisis": an,
        "fuente_hoja": meta,
        "fuentes": {
            "consulta": f.get("consulta", {}),
            "lacuerda": {"estado": lc.get("estado", "sin buscar"),
                         "candidatos": lc.get("candidatos", [])[:8]},
            "letra": {k: v for k, v in letra.items()
                      if k in ("estado", "tema", "artista", "album", "duracion",
                               "id", "puntaje", "motivos", "alternativas")}
                     | {"sincronizada": bool(sinc)},
            "cifraclub": f.get("cifraclub", {}),
        },
    }


def build(a: Analysis, outdir: Path) -> Path:
    data = armar(a, outdir)
    html = _TEMPLATE.replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False)
                             .replace("</", "<\\/"))
    html = html.replace("/*__SHAPES__*/", JS_SHAPES + _svg_diagram_js())
    html = html.replace("__TITULO__", _esc(data["titulo"]))
    path = outdir / "cancion.html"
    path.write_text(html, encoding="utf-8")
    return path


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_TEMPLATE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITULO__ · ChordLocal</title>
<style>
:root{--bg:#fbfaf7;--fg:#17160f;--mut:#6b6658;--line:#e3ded2;--card:#fff;
  --acc:#9a5b2d;--acc-soft:#f4e8dc;--hl:#fff3c4;--ok:#2f6b46;--warn:#8a6414;
  color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#13120f;--fg:#ecebe2;--mut:#9a948a;--line:#2c2a25;--card:#1b1a16;
  --acc:#e0a368;--acc-soft:#2f2419;--hl:#3a3216;--ok:#6cc08c;--warn:#d9b45f;color-scheme:dark}}
:root[data-theme=dark]{--bg:#13120f;--fg:#ecebe2;--mut:#9a948a;--line:#2c2a25;
  --card:#1b1a16;--acc:#e0a368;--acc-soft:#2f2419;--hl:#3a3216;--ok:#6cc08c;
  --warn:#d9b45f;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
a{color:var(--acc)}
.wrap{max-width:960px;margin:0 auto;padding:22px 18px 45vh}
header h1{font-size:1.6rem;margin:0;letter-spacing:-.02em;line-height:1.2}
.sub{color:var(--mut);font-size:.92rem;margin-top:2px}
.facts{display:flex;flex-wrap:wrap;gap:7px;margin:12px 0 4px}
.fact{background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:4px 10px;font-size:.82rem}
.fact b{color:var(--acc)}
/* --- reproductor --- */
.player{position:sticky;top:0;z-index:30;background:var(--bg);
  border-bottom:1px solid var(--line);padding:10px 0 8px;margin:10px 0 0}
.prow{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.now{display:flex;align-items:baseline;gap:10px;min-width:170px}
.now .cur{font-size:2rem;font-weight:750;color:var(--acc);min-width:2.5em;
  font-variant-numeric:tabular-nums;line-height:1}
.now .nxt{color:var(--mut);font-size:1rem}
.time{font-variant-numeric:tabular-nums;color:var(--mut);font-size:.82rem}
.tl{position:relative;height:30px;margin-top:8px;border-radius:7px;overflow:hidden;
  background:var(--card);border:1px solid var(--line);cursor:pointer}
.tl .sg{position:absolute;top:0;bottom:0;border-right:1px solid var(--bg);
  font-size:.68rem;color:var(--fg);padding:7px 3px 0;overflow:hidden;white-space:nowrap}
.tl .ph{position:absolute;top:0;bottom:0;width:2px;background:var(--fg);pointer-events:none}
.tools{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-top:8px}
.grp{display:flex;align-items:center;gap:4px;font-size:.8rem;color:var(--mut)}
button{font:inherit;font-size:.84rem;background:var(--card);color:var(--fg);
  border:1px solid var(--line);border-radius:7px;padding:5px 10px;cursor:pointer}
button:hover{border-color:var(--acc)}
button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
button.big{font-size:1rem;padding:7px 14px}
.val{min-width:2.2em;text-align:center;font-weight:650;color:var(--fg);
  font-variant-numeric:tabular-nums}
.tabs{display:flex;gap:4px;margin:16px 0 14px;border-bottom:1px solid var(--line);flex-wrap:wrap}
.tabs button{border:0;border-bottom:2px solid transparent;border-radius:0;
  background:none;padding:8px 12px;color:var(--mut)}
.tabs button.on{color:var(--fg);border-bottom-color:var(--acc);background:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;
  padding:14px 16px;margin-bottom:12px}
.card p{margin:0 0 8px}.card p:last-child{margin:0}
.muted{color:var(--mut)} .k{color:var(--acc);font-weight:650}
.ok{color:var(--ok);font-weight:600}.warn{color:var(--warn);font-weight:600}
h3{font-size:.98rem;margin:20px 0 8px}
/* --- hoja --- */
#hoja{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:var(--sz,15px)}
.ln{display:flex;flex-wrap:wrap;align-items:flex-end;margin:0 0 3px;border-radius:5px;
  padding:1px 4px;transition:background .2s}
.ln.sol{margin-bottom:8px}
.ln.act,.solo-letra.act{background:var(--hl)}
.seg{display:inline-flex;flex-direction:column;align-items:flex-start}
.chd{font-weight:700;color:var(--acc);font-size:.86em;line-height:1.3;white-space:pre;cursor:pointer}
.chd.act{background:var(--acc);color:#fff;border-radius:3px}
.lyr{white-space:pre;line-height:1.45}
.blanco{height:.8em}
.seccion{font-family:ui-sans-serif,system-ui,sans-serif;font-weight:700;font-size:.74rem;
  letter-spacing:.09em;text-transform:uppercase;color:var(--acc);background:var(--acc-soft);
  display:inline-block;padding:2px 9px;border-radius:5px;margin:16px 0 6px}
.solo-letra{white-space:pre-wrap;line-height:1.45;padding:1px 4px;border-radius:5px}
.plana{white-space:pre-wrap;font-family:inherit;line-height:1.6}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:6px}
.cb{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:6px 8px;
  min-height:46px;cursor:pointer}
.cb.act{border-color:var(--acc);background:var(--acc-soft)}
.cb .n{font-size:.64rem;color:var(--mut)} .cb .c{font-weight:650}
.dias{display:flex;flex-wrap:wrap;gap:10px}
.dia{background:var(--card);border:1px solid var(--line);border-radius:9px;
  padding:8px 5px 4px;width:76px;text-align:center}
.dia b{display:block;font-size:.82rem;margin-bottom:3px}
.dia.missing span{font-size:.68rem;color:var(--mut)}
svg.fb{width:100%;height:auto;display:block}
svg.fb line{stroke:var(--line);stroke-width:.7}
svg.fb .nut{fill:var(--fg)} svg.fb .dot{fill:var(--acc)}
svg.fb .mk{font-size:5px;fill:var(--mut);text-anchor:middle}
svg.fb .pos{font-size:5.5px;fill:var(--mut);text-anchor:end}
.tw{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.86rem;min-width:420px}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600;font-size:.74rem;text-transform:uppercase;letter-spacing:.04em}
.barra{height:7px;background:var(--acc);border-radius:4px;opacity:.75}
#pop{position:fixed;z-index:50;display:none;background:var(--card);border:1px solid var(--acc);
  border-radius:10px;padding:8px;box-shadow:0 8px 24px rgba(0,0,0,.18)}
footer{margin-top:34px;padding-top:12px;border-top:1px solid var(--line);color:var(--mut);font-size:.78rem}
textarea{width:100%;min-height:200px;font:13px/1.4 ui-monospace,Menlo,monospace;
  background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:9px}
input[type=text]{font:inherit;padding:6px 9px;border-radius:7px;border:1px solid var(--line);
  background:var(--bg);color:var(--fg);min-width:0;flex:1}
@media(max-width:640px){.grid{grid-template-columns:repeat(2,1fr)}.now .cur{font-size:1.6rem}}
@media print{.player,.tabs,footer,#pop{display:none}.pane{display:block!important}}
</style></head><body><div class="wrap">

<header>
  <h1 id="ttl"></h1>
  <div class="sub" id="art"></div>
  <div class="facts" id="facts"></div>
</header>

<div class="player" id="player">
  <div class="prow">
    <button class="big" id="play">▶</button>
    <div class="now"><span class="cur" id="cur">–</span><span class="nxt" id="nxt"></span></div>
    <span class="time" id="time"></span>
    <span class="muted" id="noaudio" hidden style="font-size:.8rem">Audio no disponible: la hoja igual se puede leer y transponer.</span>
  </div>
  <div class="tl" id="tl"></div>
  <div class="tools">
    <div class="grp">Tono <button id="tDown">−</button><span class="val" id="tVal">0</span><button id="tUp">+</button></div>
    <div class="grp">Cejilla <button id="cDown">−</button><span class="val" id="cVal">0</span><button id="cUp">+</button></div>
    <div class="grp"><button id="simp">Simplificar</button></div>
    <div class="grp">Letra <button id="zDown">A−</button><button id="zUp">A+</button></div>
    <div class="grp"><button id="seguir" class="on">Seguir</button></div>
    <div class="grp">Velocidad <button id="vDown">−</button><span class="val" id="vVal">1.0×</span><button id="vUp">+</button></div>
    <div class="grp"><button id="tema">◐</button></div>
  </div>
</div>

<div class="tabs" id="tabs">
  <button data-p="letra" class="on">Letra y acordes</button>
  <button data-p="analisis">Análisis</button>
  <button data-p="acordes">Acordes</button>
  <button data-p="fuentes">Fuentes</button>
</div>

<section class="pane" id="p-letra">
  <div id="aviso"></div>
  <div id="hoja"></div>
</section>
<section class="pane" id="p-analisis" hidden></section>
<section class="pane" id="p-acordes" hidden>
  <h3>Diagramas</h3><div class="dias" id="dias"></div>
  <h3>Progresión por compás</h3><div class="grid" id="grid"></div>
</section>
<section class="pane" id="p-fuentes" hidden></section>

<footer id="pie"></footer>
</div>
<div id="pop"></div>
<audio id="au" preload="metadata"></audio>
<script>
const D = /*__DATA__*/;
/*__SHAPES__*/
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let transpose=0, capo=0, sz=15, simple=false, seguir=true;
const tp=r=>((r+transpose)%12+12)%12;
const SIMPLE={maj:"maj",min:"min","7":"maj",min7:"min",maj7:"maj",min7b5:"min",dim:"min",
  sus4:"maj",sus2:"maj","6":"maj",min6:"min","9":"maj",maj9:"maj",min9:"min",add9:"maj",
  "5":"maj",aug:"maj",dim7:"min",minmaj7:"min"};
const qq=q=>simple?(SIMPLE[q]||q):q;
/* Ortografia segun la tonalidad que suena (sigue al transporte). */
const NATURAL=["C","C#","D","Eb","E","F","F#","G","Ab","A","Bb","B"];
function NOMBRES(){const t=((D.key.t+transpose)%12+12)%12, rel=D.key.m==="major"?t:(t+3)%12;
  return [5,10,3,8,1,6].includes(rel)?FLAT:(rel===0?NATURAL:SHARP);}
function nameOf(root,q){return NOMBRES()[((root%12)+12)%12]+(SUF[q]??"");}
const nom=(r,q)=>nameOf(tp(r),qq(q),D.flats);
function nombre(a){
  if(a.r===null||a.r===undefined) return a.t;
  const N=NOMBRES();
  if(simple) return nom(a.r,a.q)+(a.b!=null?"/"+N[tp(a.b)]:"");
  const resto=a.t.replace(/^[A-G][#b]?/,"").replace(/\/[A-G][#b]?$/,"");
  return N[tp(a.r)]+resto+(a.b!=null?"/"+N[tp(a.b)]:"");
}
const fmt=t=>Math.floor(t/60)+":"+String(Math.floor(t%60)).padStart(2,"0");

/* ---------- cabecera ---------- */
function cabecera(){
  $("ttl").textContent=D.titulo;
  $("art").textContent=[D.artista, D.titulo_video&&D.titulo_video!==D.titulo?"· "+D.titulo_video:""].filter(Boolean).join(" ");
  const h=D.fuente_hoja||{};
  $("facts").innerHTML=[
    ["Tonalidad",transpose?NOMBRES()[tp(D.key.t)]+(D.key.m==="major"?" mayor":" menor")+" ("+(transpose>0?"+":"")+transpose+")":D.tonalidad],["Tempo",Math.round(D.bpm)+" BPM"],["Compás",D.compas],
    ["Duración",fmt(D.duracion)],
    h.origen?["Acordes",h.origen+(h.colaborador?" · "+h.colaborador:"")]:["Acordes","análisis del audio"],
    D.comparacion?["Concordancia",D.comparacion.puntaje+"%"]:null,
  ].filter(Boolean).map(([k,v])=>'<span class="fact">'+k+' <b>'+esc(v)+'</b></span>').join("");
}

/* ---------- hoja ---------- */
function lineaPar(l,i){
  const txt=l.txt||"", ac=l.ac||[];
  let out="";
  if(ac.length&&ac[0].col>0) out+='<span class="seg"><span class="chd"> </span><span class="lyr">'+esc(txt.slice(0,ac[0].col))+'</span></span>';
  if(!ac.length) out+='<span class="seg"><span class="chd"> </span><span class="lyr">'+esc(txt)+'</span></span>';
  ac.forEach((a,j)=>{
    const hasta=j+1<ac.length?ac[j+1].col:Math.max(txt.length,a.col);
    const trozo=txt.slice(a.col,hasta), n=nombre(a);
    out+='<span class="seg"><span class="chd"'+(a.s!=null?' data-s="'+a.s+'"':'')+' data-r="'+a.r+'" data-q="'+a.q+'">'+
      esc(n)+(trozo.length>n.length?"":" ")+'</span><span class="lyr">'+esc(trozo)+'</span></span>';
  });
  return '<div class="ln" data-i="'+i+'">'+out+'</div>';
}
function hoja(){
  if(D.modo==="hoja"||D.modo==="sincronizada"){
    return D.lineas.map((l,i)=>{
      if(l.tipo==="blanco") return '<div class="blanco"></div>';
      if(l.tipo==="seccion") return '<div class="seccion">'+esc(l.txt)+'</div>';
      if(l.tipo==="letra") return '<div class="solo-letra" data-i="'+i+'">'+esc(l.txt)+'</div>';
      if(l.tipo==="acordes") return '<div class="ln sol" data-i="'+i+'">'+(l.ac||[]).map(a=>
        '<span class="seg"><span class="chd"'+(a.s!=null?' data-s="'+a.s+'"':'')+' data-r="'+a.r+'" data-q="'+a.q+'">'+esc(nombre(a))+'  </span><span class="lyr"></span></span>').join("")+'</div>';
      return lineaPar(l,i);
    }).join("");
  }
  let h="";
  if(D.modo==="plana") h+='<div class="card"><p class="muted">Letra sin sincronizar (LRCLIB). Los acordes del audio van en la grilla de abajo, compás por compás, y se resaltan mientras suena.</p></div>';
  h+=grillaHTML();
  if(D.modo==="plana") h+='<h3>Letra</h3><div class="plana">'+esc(D.letra_plana)+'</div>';
  return h;
}
function compases(){
  const bars={};
  for(const c of D.timeline){
    const b=c.b||1; (bars[b]=bars[b]||{s:c.s,cs:[]});
    const n=nom(c.r,c.q); const cs=bars[b].cs;
    if(cs[cs.length-1]!==n) cs.push(n);
  }
  return bars;
}
function grillaHTML(){
  const bars=compases();
  return '<div class="grid">'+Object.keys(bars).map(Number).sort((a,b)=>a-b).map(b=>
    '<div class="cb" data-s="'+bars[b].s+'" data-b="'+b+'"><div class="n">c.'+b+'</div><div class="c">'+bars[b].cs.join(" ")+'</div></div>').join("")+'</div>';
}

function aviso(){
  const p=[], c=D.comparacion, h=D.fuente_hoja||{};
  if(D.modo==="hoja"&&c){
    let t='<b>Acordes de '+esc(h.origen||"la transcripción")+'</b>'+(h.colaborador?' (transcripción de '+esc(h.colaborador)+')':'')+
      ', contrastados con el audio: concuerdan un <span class="k">'+c.puntaje+'%</span>.';
    if(D.llevada){
      t+=' La transcripción está escrita '+Math.abs(D.llevada)+' semitono'+(Math.abs(D.llevada)===1?'':'s')+' '+(D.llevada>0?'arriba':'abajo')+
        ' de la grabación (afinación o versión distinta); '+(transpose===0?'se muestra en el tono que suena. <button id="orig">Ver en el tono escrito</button>':
        transpose===D.llevada?'se muestra en el tono escrito. <button id="fix">Volver al tono de la grabación</button>':'');
    }
    if(c.puntaje<40) t+='<br><span class="warn">Concordancia baja:</span> puede ser otra versión del tema. Revisá la pestaña Fuentes.';
    if(D.con_tiempo) t+='<br><span class="muted">'+D.con_tiempo+' líneas sincronizadas con la letra de LRCLIB: la hoja sigue al audio.</span>';
    p.push('<div class="card">'+t+'</div>');
  } else if(D.modo==="sincronizada"){
    p.push('<div class="card"><b>Letra sincronizada (LRCLIB) con los acordes detectados en el audio.</b> '+
      '<span class="muted">No se encontró una transcripción de acordes que concuerde; los acordes salen del análisis propio y caen sobre la sílaba que se canta en ese momento.</span></div>');
  } else if(D.modo==="grilla"){
    p.push('<div class="card"><b>Sin letra.</b> <span class="muted">No se encontró letra ni transcripción online. Podés pegar una transcripción en la pestaña Fuentes.</span></div>');
  }
  $("aviso").innerHTML=p.join("");
  if($("fix")) $("fix").onclick=()=>{transpose=0;todo();};
  if($("orig")) $("orig").onclick=()=>{transpose=D.llevada;todo();};
}

/* ---------- analisis ---------- */
function analisis(){
  const A=D.analisis; let h='<div class="card">'+A.parrafos.map(p=>'<p>'+p+'</p>').join("")+'</div>';
  if(A.secciones.length) h+='<h3>Estructura (según la transcripción)</h3><div class="card">'+A.secciones.map(esc).join(" → ")+'</div>';
  h+='<h3>Acordes por tiempo sonado</h3><div class="card tw"><table><tr><th>Acorde</th><th>Grado</th><th>Función</th><th>% del tema</th><th></th></tr>'+
    A.tabla.map(d=>'<tr><td><b>'+esc(d.label)+'</b></td><td>'+esc(d.romano)+'</td><td>'+esc(d.funcion)+(d.diatonico?'':' <span class="warn">fuera de escala</span>')+
    '</td><td>'+d.pct+'%</td><td style="width:30%"><div class="barra" style="width:'+Math.min(100,d.pct*2)+'%"></div></td></tr>').join("")+'</table></div>';
  h+='<p class="muted" style="font-size:.82rem">Los grados se calculan en la tonalidad detectada del audio; el transporte de la barra no los afecta. '+
    (D.refinado?'Los acordes del audio fueron re-decididos usando el vocabulario de la transcripción online.':'')+'</p>';
  $("p-analisis").innerHTML=h;
}

/* ---------- fuentes ---------- */
function fuentes(){
  const F=D.fuentes||{}, lc=F.lacuerda||{}, le=F.letra||{}, cc=F.cifraclub||{}, q=F.consulta||{};
  let h='<div class="card"><p>Búsqueda: <b>'+esc(q.titulo||"—")+'</b>'+(q.artista?' · '+esc(q.artista):'')+'</p>'+
    '<div class="prow app-only" hidden><input type="text" id="qT" placeholder="Tema" value="'+esc(q.titulo||"")+'">'+
    '<input type="text" id="qA" placeholder="Artista" value="'+esc(q.artista||"")+'"><button id="rebuscar">Buscar de nuevo</button></div>'+
    '<p class="muted" id="rbEstado"></p></div>';
  h+='<h3>LaCuerda.net</h3><div class="card">';
  if(lc.candidatos&&lc.candidatos.length){
    h+='<div class="tw"><table><tr><th>Versión</th><th>Título</th><th>Concordancia con el audio</th><th></th></tr>'+lc.candidatos.map((c,i)=>{
      const cc2=c.concordancia;
      return '<tr><td><a href="'+esc(c.url)+'" target="_blank" rel="noopener">'+esc(c.artista)+' — '+esc(c.tema)+'</a>'+
      (c.colaborador?'<br><span class="muted">por '+esc(c.colaborador)+'</span>':'')+'</td><td>'+c.puntaje+'</td><td>'+
      (cc2?cc2.puntaje+'%'+(cc2.offset?' ('+(cc2.offset>0?'+':'')+cc2.offset+' st)':''):'<span class="muted">no leída</span>')+'</td><td>'+
      (cc2?'<button class="app-only usar" hidden data-u="'+esc(c.url)+'">Usar esta</button>':'')+'</td></tr>';}).join("")+'</table></div>';
  } else h+='<p class="muted">'+esc(lc.estado||"sin buscar")+'</p>';
  h+='</div><h3>Letra (LRCLIB)</h3><div class="card">'+
    (le.estado==="ok"?'<p>'+esc(le.artista)+' — '+esc(le.tema)+(le.album?' <span class="muted">('+esc(le.album)+')</span>':'')+
      ' · '+(le.sincronizada?'<span class="ok">sincronizada</span>':'sin sincronizar')+
      ' · duración '+fmt(le.duracion||0)+' <span class="muted">('+(le.motivos||[]).join(", ")+')</span></p>':'<p class="muted">'+esc(le.estado||"sin buscar")+'</p>')+'</div>';
  h+='<h3>Cifra Club</h3><div class="card"><p>'+
    (cc.estado==="ok"?'<span class="ok">Cifra descargada.</span>':
     cc.estado==="bloqueado"?'Cifra Club rechaza clientes automatizados, así que no se descarga sola. Abrila en tu navegador y traéla con el marcador o pegándola acá abajo.':
     esc(cc.estado||"sin intentar"))+'</p><p>'+
    (cc.probable?'<a href="'+esc(cc.probable)+'" target="_blank" rel="noopener">Abrir la cifra probable</a> · ':'')+
    (cc.busqueda?'<a href="'+esc(cc.busqueda)+'" target="_blank" rel="noopener">Buscar en Cifra Club</a>':'')+'</p></div>';
  h+='<h3>Pegar una transcripción</h3><div class="card"><p class="muted">Copiá el bloque de acordes sobre letra de cualquier sitio (Cifra Club, Ultimate Guitar, LaCuerda) y pegalo acá. Acepta cifrado americano y latino (LA7, DO#m). Gana sobre lo encontrado automáticamente.</p>'+
    '<div class="app-only" hidden><input type="text" id="impUrl" placeholder="URL de origen (opcional)" style="width:100%;margin-bottom:6px">'+
    '<textarea id="impTxt" placeholder="Am        F&#10;Letra de la canción..."></textarea>'+
    '<div class="prow" style="margin-top:8px"><button id="impBtn">Usar esta transcripción</button><button id="impBorrar">Quitar la importada</button><span class="muted" id="impEstado"></span></div></div>'+
    '<p class="file-only muted">Abrí esta hoja desde la app (<code>chordlocal app</code>) para pegar o re-buscar.</p></div>';
  $("p-fuentes").innerHTML=h;
}

/* ---------- acordes ---------- */
function acordes(){
  $("dias").innerHTML=dedup(D.vocabulario.map(c=>({r:tp(c.r),q:qq(c.q)}))).map(c=>
    diagramSVG(fingering(c.r,c.q,capo),nameOf(c.r,c.q,D.flats))).join("");
  $("grid").innerHTML=grillaHTML();
}
function dedup(xs){const s=new Set(),o=[];for(const c of xs){const k=c.r+"|"+c.q;if(!s.has(k)){s.add(k);o.push(c);}}return o;}

/* ---------- linea de tiempo ---------- */
const PAL=["#d9a26b","#8fb27a","#7aa6c2","#c48bb3","#c9b25b","#7fbfb0","#d48a7a","#a59ad1","#b8a27e","#9cc17d","#e0a0a0","#88a8a0"];
function timeline(){
  const T=D.duracion||1;
  $("tl").innerHTML=D.timeline.map(c=>{
    const w=100*(c.e-c.s)/T;
    return '<div class="sg" style="left:'+(100*c.s/T)+'%;width:'+w+'%;background:'+PAL[tp(c.r)]+'33">'+(w>2.2?esc(nom(c.r,c.q)):"")+'</div>';
  }).join("")+'<div class="ph" id="ph"></div>';
}

function todo(){
  cabecera();aviso();
  $("hoja").innerHTML=hoja();$("hoja").style.setProperty("--sz",sz+"px");
  acordes();timeline();
  $("tVal").textContent=(transpose>0?"+":"")+transpose;$("cVal").textContent=capo;
  $("simp").className=simple?"on":"";
  lineas=[...document.querySelectorAll("#hoja [data-i]")].map(el=>({el,t:(D.lineas[+el.dataset.i]||{}).t0})).filter(x=>x.t!=null);
  chips=[...document.querySelectorAll("#hoja [data-s]")].map(el=>({el,t:+el.dataset.s}));
  ultimaLinea=null;ultimoChip=null;
  sync(true);
}

/* ---------- audio y sincronizacion ---------- */
const au=$("au");
const enApp=location.protocol.startsWith("http");
au.src=enApp?D.audio.app:D.audio.archivo;
let lineas=[],chips=[],ultimaLinea=null,ultimoChip=null,hayAudio=true;
au.addEventListener("error",()=>{hayAudio=false;$("play").disabled=true;$("noaudio").hidden=false;});
function idx(t){let lo=0,hi=D.timeline.length-1,r=-1;while(lo<=hi){const m=(lo+hi)>>1;if(D.timeline[m].s<=t){r=m;lo=m+1}else hi=m-1}return r;}
function sync(force){
  const t=au.currentTime||0, i=idx(t);
  const c=D.timeline[i], n=D.timeline[i+1];
  $("cur").textContent=c&&t<c.e+0.5?nom(c.r,c.q):"–";
  $("nxt").textContent=n?"→ "+nom(n.r,n.q):"";
  $("time").textContent=fmt(t)+" / "+fmt(D.duracion);
  const ph=$("ph"); if(ph) ph.style.left=(100*t/(D.duracion||1))+"%";
  // linea activa
  let L=null; for(const x of lineas){if(x.t<=t+0.15) L=x; else break;}
  if(L!==ultimaLinea){ultimaLinea&&ultimaLinea.el.classList.remove("act");L&&L.el.classList.add("act");ultimaLinea=L;
    if(L&&seguir&&!au.paused) L.el.scrollIntoView({block:"center",behavior:"smooth"});}
  let C=null; for(const x of chips){if(x.t<=t+0.05) C=x;}
  if(C!==ultimoChip){ultimoChip&&ultimoChip.el.classList.remove("act");C&&C.el.classList.add("act");ultimoChip=C;}
  // grilla: compas activo
  const cbs=document.querySelectorAll(".cb.act"); cbs.forEach(e=>e.classList.remove("act"));
  if(c){const b=c.b; document.querySelectorAll('.cb[data-b="'+b+'"]').forEach(e=>{e.classList.add("act");
    if(seguir&&!au.paused&&!lineas.length&&e.closest("#p-letra")) e.scrollIntoView({block:"center",behavior:"smooth"});});}
  // sin tiempos por linea: desplazamiento proporcional
  if(!lineas.length&&D.modo==="hoja"&&seguir&&!au.paused){
    const h=$("hoja"), top=h.offsetTop, alto=h.offsetHeight;
    scrollTo({top:top+alto*(t/(D.duracion||1))-innerHeight*0.45});
  }
}
let raf=null;
function loop(){sync();raf=requestAnimationFrame(loop);}
au.addEventListener("play",()=>{$("play").textContent="⏸";loop();});
au.addEventListener("pause",()=>{$("play").textContent="▶";cancelAnimationFrame(raf);sync();});
au.addEventListener("seeked",()=>sync(true));
$("play").onclick=()=>au.paused?au.play():au.pause();
$("tl").onclick=e=>{const r=$("tl").getBoundingClientRect();au.currentTime=(e.clientX-r.left)/r.width*(D.duracion||0);sync();};

/* ---------- controles ---------- */
$("tUp").onclick=()=>{transpose=Math.min(11,transpose+1);todo()};
$("tDown").onclick=()=>{transpose=Math.max(-11,transpose-1);todo()};
$("cUp").onclick=()=>{capo=Math.min(9,capo+1);todo()};
$("cDown").onclick=()=>{capo=Math.max(0,capo-1);todo()};
$("simp").onclick=()=>{simple=!simple;todo()};
$("zUp").onclick=()=>{sz=Math.min(28,sz+1);$("hoja").style.setProperty("--sz",sz+"px")};
$("zDown").onclick=()=>{sz=Math.max(11,sz-1);$("hoja").style.setProperty("--sz",sz+"px")};
$("seguir").onclick=()=>{seguir=!seguir;$("seguir").className=seguir?"on":""};
let vel=1;
function setVel(v){vel=Math.round(Math.min(1.5,Math.max(.5,v))*100)/100;au.playbackRate=vel;au.preservesPitch=true;$("vVal").textContent=vel.toFixed(2).replace(/0$/,"")+"×";}
$("vUp").onclick=()=>setVel(vel+.05);$("vDown").onclick=()=>setVel(vel-.05);
$("tema").onclick=()=>{const r=document.documentElement;const cur=r.getAttribute("data-theme")||(matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");r.setAttribute("data-theme",cur==="dark"?"light":"dark");};
function pestana(n){
  if(!$("p-"+n)) return;
  document.querySelectorAll("#tabs button").forEach(x=>x.className=x.dataset.p===n?"on":"");
  document.querySelectorAll(".pane").forEach(p=>p.hidden=p.id!=="p-"+n);
}
document.querySelectorAll("#tabs button").forEach(b=>b.onclick=()=>{pestana(b.dataset.p);history.replaceState(null,"","#"+b.dataset.p);});
if(location.hash) pestana(location.hash.slice(1));
addEventListener("keydown",e=>{
  if(["INPUT","TEXTAREA"].includes(e.target.tagName)) return;
  if(e.code==="Space"&&hayAudio){e.preventDefault();au.paused?au.play():au.pause();}
  if(e.key==="ArrowRight"){au.currentTime+=5;} if(e.key==="ArrowLeft"){au.currentTime-=5;}
});
const pop=$("pop");
document.addEventListener("click",e=>{
  const t=e.target.closest(".chd,.cb");
  if(t&&t.dataset.s!=null&&hayAudio&&e.altKey){au.currentTime=+t.dataset.s;return;}
  if(!t||!t.classList.contains("chd")||t.dataset.r===undefined||t.dataset.r==="null"){pop.style.display="none";
    if(t&&t.classList.contains("cb")&&hayAudio){au.currentTime=+t.dataset.s;sync();} return;}
  const r=tp(+t.dataset.r),q=qq(t.dataset.q);
  pop.innerHTML=diagramSVG(fingering(r,q,capo),nameOf(r,q,D.flats))+(t.dataset.s&&hayAudio?'<div style="text-align:center"><button id="ir">▶ desde acá</button></div>':'');
  pop.style.display="block";const b=t.getBoundingClientRect();
  pop.style.left=Math.min(b.left,innerWidth-110)+"px";pop.style.top=(b.bottom+8)+"px";
  if($("ir")) $("ir").onclick=()=>{au.currentTime=+t.dataset.s;au.play();pop.style.display="none";};
});

/* ---------- acciones de la app ---------- */
async function post(url,body){const r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});return r.json();}
function acciones(){
  if(!enApp){document.querySelectorAll(".file-only").forEach(e=>e.hidden=false);return;}
  document.querySelectorAll(".app-only").forEach(e=>e.hidden=false);
  document.querySelectorAll(".file-only").forEach(e=>e.hidden=true);
  const rb=$("rebuscar");
  if(rb) rb.onclick=async()=>{rb.disabled=true;$("rbEstado").textContent="Buscando (LaCuerda pide 5 s entre páginas)...";
    const j=await post("/api/fuentes/"+D.slug,{titulo:$("qT").value,artista:$("qA").value});
    if(j.error){$("rbEstado").textContent=j.error;rb.disabled=false;return;}
    esperar(j.job,$("rbEstado"));};
  document.querySelectorAll(".usar").forEach(b=>b.onclick=async()=>{
    b.disabled=true;const j=await post("/api/usar/"+D.slug,{url:b.dataset.u});
    if(j.ok) location.reload(); else {b.disabled=false;alert(j.error||"error");}});
  const ib=$("impBtn");
  if(ib) ib.onclick=async()=>{const txt=$("impTxt").value;if(!txt.trim()) return;
    const j=await post("/api/importar/"+D.slug,{texto:txt,url:$("impUrl").value});
    $("impEstado").textContent=j.ok?"Listo, recargando...":(j.error||"error"); if(j.ok) location.reload();};
  const ibb=$("impBorrar");
  if(ibb) ibb.onclick=async()=>{const j=await post("/api/importar/"+D.slug,{borrar:true});if(j.ok) location.reload();};
}
async function esperar(job,el){
  const t=setInterval(async()=>{const r=await fetch("/api/estado/"+job);const j=await r.json();
    el.textContent=(j.log||[]).slice(-1)[0]||j.estado;
    if(j.estado==="listo"){clearInterval(t);location.reload();}
    if(j.estado==="error"){clearInterval(t);el.textContent=j.error;}},1200);
}

$("pie").innerHTML='Acordes, tiempos y análisis: ChordLocal, sobre el audio. '+
  (D.fuente_hoja&&D.fuente_hoja.origen?'Transcripción: '+esc(D.fuente_hoja.origen)+(D.fuente_hoja.colaborador?' ('+esc(D.fuente_hoja.colaborador)+')':'')+(D.fuente_hoja.url?' — <a href="'+esc(D.fuente_hoja.url)+'" target="_blank" rel="noopener">original</a>':'')+'. ':'')+
  (D.fuentes.letra&&D.fuentes.letra.estado==="ok"?'Letra: LRCLIB. ':'')+'Hoja de uso personal; las letras y transcripciones son obra de sus autores.';
if(D.origen&&D.origen.startsWith("http")) $("pie").innerHTML+=' · <a href="'+esc(D.origen)+'" target="_blank" rel="noopener">video original</a>';

analisis();fuentes();todo();acciones();
</script></body></html>
"""
