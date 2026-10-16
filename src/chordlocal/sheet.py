"""Hoja de cancion en HTML: una app autocontenida, sin dependencias externas.

Todo lo interactivo (transponer, cejilla, cambiar de version) ocurre en el
navegador sobre los datos ya analizados. No re-analiza nada: la transposicion es
aritmetica modulo 12, igual que en el backend.

Esta es la hoja analitica (armonia por compas, sin letra). La letra con
acordes, el reproductor y las fuentes online estan en cancion.html (pagina.py).
"""
from __future__ import annotations

import json
from pathlib import Path

from .analyze import Analysis

# Tablas de digitacion portadas a JS. Mismo contenido que theory.py, para que el
# navegador pueda recalcular formas al transponer sin volver al servidor.
JS_SHAPES = """
const STD=[40,45,50,55,59,64];
const SHARP=["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"];
const FLAT=["C","Db","D","Eb","E","F","Gb","G","Ab","A","Bb","B"];
const SUF={maj:"",min:"m",dim:"dim",aug:"aug",sus2:"sus2",sus4:"sus4","7":"7",
  maj7:"maj7",min7:"m7",min7b5:"m7b5",dim7:"dim7",minmaj7:"mMaj7","6":"6",
  min6:"m6","9":"9",maj9:"maj9",min9:"m9",add9:"add9","5":"5"};
const OPEN={"0|maj":[-1,3,2,0,1,0],"0|7":[-1,3,2,3,1,0],"0|maj7":[-1,3,2,0,0,0],
 "2|maj":[-1,-1,0,2,3,2],"2|min":[-1,-1,0,2,3,1],"2|7":[-1,-1,0,2,1,2],
 "2|min7":[-1,-1,0,2,1,1],"2|sus4":[-1,-1,0,2,3,3],"2|sus2":[-1,-1,0,2,3,0],
 "4|sus2":[0,2,4,4,0,0],"7|sus2":[3,0,0,0,3,3],"0|sus2":[-1,3,0,0,1,3],
 "9|sus2":[-1,0,2,2,0,0],"4|maj":[0,2,2,1,0,0],
 "4|min":[0,2,2,0,0,0],"4|7":[0,2,0,1,0,0],"4|min7":[0,2,2,0,3,0],
 "5|maj":[1,3,3,2,1,1],"5|maj7":[-1,-1,3,2,1,0],"7|maj":[3,2,0,0,0,3],
 "7|7":[3,2,0,0,0,1],"7|maj7":[3,2,0,0,0,2],"9|maj":[-1,0,2,2,2,0],
 "9|min":[-1,0,2,2,1,0],"9|7":[-1,0,2,0,2,0],"9|min7":[-1,0,2,0,1,0],
 "9|sus4":[-1,0,2,2,3,0],"11|min":[-1,2,4,4,3,2],"11|7":[-1,2,1,2,0,2]};
const BE={maj:[0,2,2,1,0,0],min:[0,2,2,0,0,0],"7":[0,2,0,1,0,0],min7:[0,2,0,0,0,0],
 maj7:[0,2,1,1,0,null],min7b5:[0,1,0,0,null,null],sus4:[0,2,2,2,0,0],
 "5":[0,2,2,null,null,null],"6":[0,2,0,1,2,null],"9":[0,1,0,1,1,null],
 sus2:[0,2,4,4,0,0]};
const BA={maj:[null,0,2,2,2,0],min:[null,0,2,2,1,0],"7":[null,0,2,0,2,0],
 min7:[null,0,2,0,1,0],maj7:[null,0,2,1,2,0],min7b5:[null,0,1,0,1,null],
 sus4:[null,0,2,2,3,0],dim7:[null,0,1,2,1,null],"5":[null,0,2,2,null,null],
 "6":[null,0,2,2,2,2],min6:[null,0,2,2,1,2],dim:[null,0,1,2,1,null],"9":[null,0,1,0,1,0],
 add9:[null,0,2,4,2,0],sus2:[null,0,2,2,0,0]};
function nameOf(root,q,flats){return (flats?FLAT:SHARP)[((root%12)+12)%12]+(SUF[q]??"");}
function fingering(root,q,capo){
  const shown=(((root-capo)%12)+12)%12;
  const o=OPEN[shown+"|"+q];
  if(o) return {frets:o,base:0,shape:"open"};
  let best=null;
  for(const [tbl,str,tag] of [[BE,0,"barre-E"],[BA,1,"barre-A"]]){
    const rel=tbl[q]; if(!rel) continue;
    let base=(((shown-(STD[str]%12))%12)+12)%12; if(base===0) base=12;
    const frets=rel.map(r=>r===null?-1:base+r);
    const mx=Math.max(...frets.filter(f=>f>0),0);
    if(mx>15) continue;
    const cand={frets,base,shape:tag};
    if(!best||cand.base<best.base) best=cand;
  }
  return best;
}
"""


def _svg_diagram_js() -> str:
    """Dibuja el diagrama de acorde como SVG, en el navegador."""
    return """
function diagramSVG(f,label){
  if(!f) return '<div class="dia missing"><b>'+label+'</b><span>sin forma</span></div>';
  const W=56,H=74,x0=8,y0=16,dx=8,dy=11,N=5;
  const start=f.base>0?f.base:1;
  let s='<svg viewBox="0 0 '+W+' '+H+'" class="fb" role="img" aria-label="'+label+'">';
  for(let i=0;i<6;i++) s+='<line x1="'+(x0+i*dx)+'" y1="'+y0+'" x2="'+(x0+i*dx)+'" y2="'+(y0+N*dy)+'"/>';
  for(let j=0;j<=N;j++) s+='<line x1="'+x0+'" y1="'+(y0+j*dy)+'" x2="'+(x0+5*dx)+'" y2="'+(y0+j*dy)+'"/>';
  if(f.base===0) s+='<rect x="'+(x0-1)+'" y="'+(y0-2.5)+'" width="'+(5*dx+2)+'" height="2.5" class="nut"/>';
  else s+='<text x="'+(x0-4)+'" y="'+(y0+dy-3)+'" class="pos">'+f.base+'</text>';
  f.frets.forEach((fr,i)=>{
    const x=x0+i*dx;
    if(fr<0) s+='<text x="'+x+'" y="'+(y0-4)+'" class="mk">x</text>';
    else if(fr===0) s+='<text x="'+x+'" y="'+(y0-4)+'" class="mk">o</text>';
    else{const row=fr-start; if(row>=0&&row<N) s+='<circle cx="'+x+'" cy="'+(y0+row*dy+dy/2)+'" r="3.1" class="dot"/>';}
  });
  s+='</svg>';
  return '<div class="dia"><b>'+label+'</b>'+s+'</div>';
}
"""


def build(a: Analysis, path: Path, titulo_fuente: str = "") -> Path:
    """Escribe la hoja HTML interactiva."""
    arm = a.armonia or {}
    con = a.contraste or {}
    src = a.source or {}
    ev = (con.get("evidencia") or {})
    tempo_c = con.get("tempo") or {}
    key_c = con.get("tonalidad") or {}

    ideal = [c for c in a.chords if c.get("root") is not None]
    simple = a.simple or []

    data = {
        "titulo": src.get("title", "Cancion"),
        "artista": src.get("artist", "") or ev.get("artista", ""),
        "tonalidad": arm.get("tonalidad", ""),
        "escala": arm.get("escala", []),
        "flats": (a.key or {}).get("flats", False),
        "bpm": (a.tempo or {}).get("bpm"),
        "bpm_sugerido": tempo_c.get("sugerido"),
        "compas": (a.tempo or {}).get("meter", "4/4"),
        "duracion": src.get("duration", 0),
        "capo": arm.get("capo_sugerido"),
        "pedal": arm.get("pedal"),
        "n_ambiguos": arm.get("n_ambiguos", 0),
        "ambiguos": arm.get("ambiguos", [])[:12],
        "alternativas": arm.get("alternativas", []),
        "grados": arm.get("grados", []),
        "frases": arm.get("frases", []),
        "ideal": [{"b": c["bar"], "r": c["root"], "q": c["quality"]} for c in ideal],
        "simple": [{"b": c["bar"], "r": c["root"], "q": c["quality"]} for c in simple],
        "contraste": {
            "fuentes": ev.get("fuentes", []),
            "bpm_ext": ev.get("bpm"),
            "key_ext": (f"{ev.get('tonica')} {ev.get('modo')}"
                        if ev.get("tonica") else None),
            "tempo_veredicto": tempo_c.get("veredicto"),
            "key_veredicto": key_c.get("veredicto"),
            "notas": ev.get("notas", []),
        },
    }

    html = _TEMPLATE.replace("/*__DATA__*/", json.dumps(data, ensure_ascii=False))
    html = html.replace("/*__SHAPES__*/", JS_SHAPES + _svg_diagram_js())
    html = html.replace("__TITULO__", data["titulo"])

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


_TEMPLATE = r"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITULO__ · ChordLocal</title>
<style>
:root{
  --bg:#fbfaf7; --fg:#16150f; --mut:#6b6658; --line:#e2ddd0; --card:#fff;
  --acc:#9a5b2d; --acc-soft:#f3e7db; --ok:#2f6b46; --warn:#8a6414;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#13120f; --fg:#eceadf; --mut:#9a948a; --line:#2b2924; --card:#1b1a16;
  --acc:#d99a5f; --acc-soft:#2e2318; --ok:#6cc08c; --warn:#d9b45f;}}
:root[data-theme=dark]{--bg:#13120f;--fg:#eceadf;--mut:#9a948a;--line:#2b2924;
  --card:#1b1a16;--acc:#d99a5f;--acc-soft:#2e2318;--ok:#6cc08c;--warn:#d9b45f;}
:root[data-theme=light]{--bg:#fbfaf7;--fg:#16150f;--mut:#6b6658;--line:#e2ddd0;
  --card:#fff;--acc:#9a5b2d;--acc-soft:#f3e7db;--ok:#2f6b46;--warn:#8a6414;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 ui-sans-serif,system-ui,-apple-system,sans-serif;}
.wrap{max-width:980px;margin:0 auto;padding:28px 20px 80px}
header{border-bottom:1px solid var(--line);padding-bottom:18px;margin-bottom:22px}
h1{font-size:1.75rem;margin:0 0 2px;letter-spacing:-.02em}
.sub{color:var(--mut);font-size:.95rem}
.facts{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.fact{background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:6px 11px;font-size:.84rem}
.fact b{color:var(--acc);font-weight:600}
.bar{position:sticky;top:0;z-index:10;background:var(--bg);
  border-bottom:1px solid var(--line);padding:10px 0;margin-bottom:22px;
  display:flex;flex-wrap:wrap;gap:14px;align-items:center}
.grp{display:flex;align-items:center;gap:6px;font-size:.84rem;color:var(--mut)}
button{font:inherit;font-size:.86rem;background:var(--card);color:var(--fg);
  border:1px solid var(--line);border-radius:7px;padding:5px 11px;cursor:pointer}
button:hover{border-color:var(--acc)}
button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.val{min-width:2.4em;text-align:center;font-variant-numeric:tabular-nums;
  font-weight:600;color:var(--fg)}
h2{font-size:1.05rem;margin:30px 0 12px;letter-spacing:-.01em;
  display:flex;align-items:center;gap:9px}
h2::after{content:"";flex:1;height:1px;background:var(--line)}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;
  padding:16px 18px;margin-bottom:14px}
.card p{margin:0 0 9px} .card p:last-child{margin:0}
.lede{font-size:.97rem}
.k{color:var(--acc);font-weight:600}
.dias{display:flex;flex-wrap:wrap;gap:12px}
.dia{background:var(--card);border:1px solid var(--line);border-radius:9px;
  padding:9px 6px 5px;width:78px;text-align:center}
.dia b{display:block;font-size:.84rem;margin-bottom:3px}
.dia.missing span{font-size:.7rem;color:var(--mut)}
svg.fb{width:100%;height:auto;display:block}
svg.fb line{stroke:var(--line);stroke-width:.7}
svg.fb .nut{fill:var(--fg)}
svg.fb .dot{fill:var(--acc)}
svg.fb .mk{font-size:5px;fill:var(--mut);text-anchor:middle}
svg.fb .pos{font-size:5.5px;fill:var(--mut);text-anchor:end}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}
.cellbar{background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:9px 10px;min-height:56px}
.cellbar .n{font-size:.68rem;color:var(--mut)}
.cellbar .c{font-weight:600;font-size:.98rem;margin-top:2px}
.cellbar .rn{font-size:.72rem;color:var(--acc);font-style:italic}
.tablewrap{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:.87rem;min-width:420px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-weight:600;font-size:.78rem;text-transform:uppercase;
  letter-spacing:.04em}
.ok{color:var(--ok);font-weight:600} .warn{color:var(--warn);font-weight:600}
.muted{color:var(--mut)}
ul{margin:6px 0 0;padding-left:19px} li{margin:3px 0}
footer{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);
  color:var(--mut);font-size:.8rem}
@media(max-width:640px){.grid{grid-template-columns:repeat(2,1fr)}}
</style></head><body><div class="wrap">

<header>
  <h1 id="ttl"></h1>
  <div class="sub" id="art"></div>
  <div class="facts" id="facts"></div>
</header>

<div class="bar">
  <div class="grp"><span>Versión</span>
    <button id="bIdeal" class="on">Ideal</button>
    <button id="bSimple">Simplificada</button></div>
  <div class="grp"><span>Transponer</span>
    <button id="tDown">−</button><span class="val" id="tVal">0</span>
    <button id="tUp">+</button></div>
  <div class="grp"><span>Cejilla</span>
    <button id="cDown">−</button><span class="val" id="cVal">0</span>
    <button id="cUp">+</button></div>
  <div class="grp"><button id="tema">Tema</button></div>
</div>

<section id="intro"></section>
<h2>Acordes</h2><div class="dias" id="dias"></div>
<h2>Progresión por compás</h2><div class="grid" id="grid"></div>
<h2>Contraste con fuentes externas</h2><div id="contraste"></div>

<footer>
  Generado por ChordLocal a partir del audio. El análisis armónico es automático
  y contrastado con datos abiertos; verificá de oído antes de darlo por definitivo.
  La letra con acordes está en cancion.html.
</footer>
</div>
<script>
const D = /*__DATA__*/;
/*__SHAPES__*/

let transpose=0, capo=0, modo="ideal";
const $=id=>document.getElementById(id);

function chords(){ return modo==="ideal"? D.ideal : D.simple; }

function render(){
  const flats=D.flats;
  $("ttl").textContent=D.titulo;
  $("art").textContent=D.artista||"";

  // Tonalidad mostrada, ya transpuesta.
  const e0=D.escala.length?D.escala[0]:"C";
  const base=Math.max(0,SHARP.indexOf(e0)>=0?SHARP.indexOf(e0):FLAT.indexOf(e0));
  const keyNow=(flats?FLAT:SHARP)[((base+transpose)%12+12)%12];
  const modoTxt=(D.tonalidad||"").includes("menor")?"menor":"mayor";

  const bpm=D.bpm_sugerido||D.bpm;
  $("facts").innerHTML=[
    ["Tonalidad",keyNow+" "+modoTxt],
    ["Tempo",(bpm?bpm+" BPM":"—")],
    ["Compás",D.compas],
    ["Duración",Math.round(D.duracion)+" s"],
    transpose?["Transpuesto",(transpose>0?"+":"")+transpose+" st"]:null,
    capo?["Cejilla","traste "+capo]:null,
  ].filter(Boolean).map(([k,v])=>'<span class="fact">'+k+' <b>'+v+'</b></span>').join("");

  // Acordes unicos, en orden de aparicion.
  const seen=[],key=new Set();
  for(const c of chords()){
    const r=((c.r+transpose)%12+12)%12, k=r+"|"+c.q;
    if(!key.has(k)){key.add(k);seen.push({r,q:c.q});}
  }
  $("dias").innerHTML=seen.map(c=>
    diagramSVG(fingering(c.r,c.q,capo),nameOf(c.r,c.q,flats))).join("");

  // Grilla por compas.
  const bars={};
  for(const c of chords()){
    const r=((c.r+transpose)%12+12)%12, n=nameOf(r,c.q,flats);
    (bars[c.b]=bars[c.b]||[]);
    if(bars[c.b][bars[c.b].length-1]!==n) bars[c.b].push(n);
  }
  const rn={}; for(const g of (D.grados||[])) if(!(g.bar in rn)) rn[g.bar]=g.romano;
  $("grid").innerHTML=Object.keys(bars).map(Number).sort((a,b)=>a-b).slice(0,64)
    .map(b=>'<div class="cellbar"><div class="n">c.'+b+'</div><div class="c">'+
      bars[b].join(" ")+'</div><div class="rn">'+(modo==="ideal"&&rn[b]?rn[b]:"")+
      '</div></div>').join("");

  $("tVal").textContent=(transpose>0?"+":"")+transpose;
  $("cVal").textContent=capo;
  $("bIdeal").className=modo==="ideal"?"on":"";
  $("bSimple").className=modo==="simple"?"on":"";
}

function intro(){
  const p=[];
  const escala=(D.escala||[]).join(" ");
  p.push('<div class="card lede"><p>La canción está en <span class="k">'+
    D.tonalidad+'</span>, con la escala <span class="k">'+escala+'</span>. '+
    'Todo acorde que caiga fuera de esas siete notas es un color prestado, '+
    'y vale la pena oírlo como tal.</p>');
  if(D.capo) p.push('<p><b>Cejilla sugerida:</b> traste '+D.capo.capo+
    ' y tocar con las formas de <span class="k">'+D.capo.forma+
    '</span>. Suena igual, pero se digita con acordes abiertos.</p>');
  if(D.pedal) p.push('<p><b>Pedal de '+D.pedal.nota+':</b> el bajo se queda en '+
    D.pedal.nota+' durante el '+Math.round(D.pedal.fraccion*100)+
    '% del tema. Muchos acordes que parecen distintos son en realidad el mismo '+
    'bajo con las voces de arriba cambiando.</p>');
  if(D.n_ambiguos) p.push('<p><b>'+D.n_ambiguos+' acordes sin tercera definida.</b> '+
    'En esos tramos el audio no define si es mayor o menor: suelen ser voicings '+
    'con cuerdas al aire o quintas. No es un error del análisis, es cómo está '+
    'tocada la canción — y te da libertad para elegir.</p>');
  if((D.alternativas||[]).length) p.push('<p class="muted">Otras lecturas posibles '+
    'de la tonalidad: '+D.alternativas.slice(0,2).join(", ")+'.</p>');
  p.push('</div>');
  $("intro").innerHTML=p.join("");
}

function contraste(){
  const c=D.contraste||{};
  const rows=[];
  const vd=v=>v==="coincide"||v==="relativa (mismas notas)"
    ?'<span class="ok">'+v+'</span>'
    :(v&&v!=="sin contraste"?'<span class="warn">'+v+'</span>':'<span class="muted">—</span>');
  rows.push(["Tonalidad",D.tonalidad,c.key_ext||"—",vd(c.key_veredicto)]);
  rows.push(["Tempo",(D.bpm||"—")+" BPM",(c.bpm_ext?c.bpm_ext+" BPM":"—"),
             vd(c.tempo_veredicto)]);
  let h='<div class="card"><div class="tablewrap"><table><tr><th>Dato</th>'+
    '<th>Detectado</th><th>Fuente externa</th><th>Veredicto</th></tr>';
  h+=rows.map(r=>'<tr><td>'+r[0]+'</td><td>'+r[1]+'</td><td>'+r[2]+'</td><td>'+
    r[3]+'</td></tr>').join("")+'</table></div>';
  h+='<p class="muted" style="margin-top:10px">Fuentes: '+
    ((c.fuentes||[]).join(", ")||"ninguna disponible")+
    '. La letra y los acordes encontrados online están en la hoja principal (cancion.html).</p>';
  if((c.notas||[]).length) h+='<ul class="muted">'+c.notas.map(n=>'<li>'+n+'</li>').join("")+'</ul>';
  h+='</div>';
  $("contraste").innerHTML=h;
}

$("tUp").onclick=()=>{transpose=Math.min(11,transpose+1);render()};
$("tDown").onclick=()=>{transpose=Math.max(-11,transpose-1);render()};
$("cUp").onclick=()=>{capo=Math.min(9,capo+1);render()};
$("cDown").onclick=()=>{capo=Math.max(0,capo-1);render()};
$("bIdeal").onclick=()=>{modo="ideal";render()};
$("bSimple").onclick=()=>{modo="simple";render()};
$("tema").onclick=()=>{
  const r=document.documentElement;
  const cur=r.getAttribute("data-theme")||
    (matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
  r.setAttribute("data-theme",cur==="dark"?"light":"dark");
};

intro();contraste();render();
</script></body></html>
"""
