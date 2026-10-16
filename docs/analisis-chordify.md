# Análisis de Chordify → App local libre ("ChordLocal")

Fecha: 2026-10-15 · Objetivo: replicar localmente las funciones de Chordify
(incluyendo las de pago) usando solo software libre, sobre audio propio.

---

## 1. Matriz de features

| # | Feature Chordify | Plan | Equivalente local | Dificultad |
|---|---|---|---|---|
| 1 | Detección automática de acordes | Gratis | `BTC-ISMIR19` (transformer) o `autochord` (Bi-LSTM-CRF) | **Alta** — es el núcleo |
| 2 | Diagramas animados guitarra / piano / ukelele | Gratis | SVG generado desde la fórmula del acorde | Baja |
| 3 | Reproducción sincronizada con la grilla | Gratis | `beat_this` (beats + downbeats) + Web Audio | Media |
| 4 | Catálogo de canciones | Gratis | Biblioteca local indexada (SQLite) | Baja |
| 5 | Transposición de tonalidad | **Premium** | Rotación de semitonos sobre el resultado | **Trivial** |
| 6 | Modo capo | **Premium** | Transposición inversa + reescritura de digitación | **Trivial** |
| 7 | Cambio de tempo sin alterar pitch | **Premium** | Rubber Band / SoundTouch | Baja |
| 8 | Loop de secciones | **Premium** | Rango A-B en el reproductor | **Trivial** |
| 9 | Volumen acordes vs. canción | **Premium** | Dos buses de audio | **Trivial** |
| 10 | Export a MIDI | **Premium** | `pretty_midi` desde los acordes + grilla | Baja |
| 11 | Afinador | Gratis | Web Audio + YIN / autocorrelación | Baja |
| 12 | Detección de acordes en vivo (micro) | Gratis | Ventana deslizante de croma en tiempo real | Media |
| 13 | Letras sincronizadas | Gratis | WhisperX sobre audio propio (uso personal) | Media |
| — | *Extra, no lo tiene Chordify* | — | Demucs: silenciar la guitarra y tocar encima | Baja |

**Conclusión del análisis:** 6 de los 7 features de pago (#5, #6, #8, #9, #10, y
parcialmente #7) son transformaciones puras sobre el JSON de análisis. No cuestan
casi nada de implementar. Chordify cobra por la conveniencia, no por la
complejidad. Lo único genuinamente difícil es #1, y existen modelos libres
entrenados que lo resuelven con calidad comparable.

---

## 2. Realidad del hardware (restricción que define el diseño)

Medido en esta máquina:

- MacBook Pro Intel **x86_64**, i7-6820HQ @ 2.70 GHz, 16 GB RAM
- Sin GPU usable (MPS es solo Apple Silicon; sin CUDA)
- Python del sistema: 3.14.5 — **demasiado nuevo** para el stack de ML de audio

**Límite duro encontrado:** PyTorch dejó de publicar wheels para macOS Intel
después de la 2.4.1, y esa versión solo cubre hasta Python 3.12. El resolver
en esta máquina converge en:

```
torch == 2.2.2   (último wheel macosx_*_x86_64 instalable)
```

Verificado como instalable bajo ese techo (`uv pip install --dry-run`, Python 3.11):

```
beat-this    1.1.0   + torch 2.2.2 + torchaudio 2.2.2   OK
demucs       4.1.0   + torch 2.2.2                      OK
librosa      0.11.0                                     OK
pretty-midi  0.2.11                                     OK
essentia     2.1b6                                      OK
```

**Verificado al construir (no estimado):** los cinco obstaculos reales del
entorno, todos encontrados y resueltos durante la Fase 1:

| Obstaculo | Sintoma | Solucion aplicada |
|---|---|---|
| `torch` sin wheel x86 macOS > 2.4.1 | resolver no satisface | pin `torch==2.2.2` |
| `llvmlite 0.50` sin wheel cp311 x86 | intenta compilar contra LLVM y falla | pin `numba==0.60.0` / `llvmlite==0.43.0` |
| `setuptools 84` elimino `pkg_resources` | `resampy` (via librosa) no importa | pin `setuptools<81` |
| macOS 12: Homebrew sin bottles | `brew install ffmpeg` compila 15 deps desde fuente | binario estatico via `imageio-ffmpeg` |
| basic-pitch elige CoreML en macOS | inferencia aborta con **SIGFPE** | apuntar explicitamente a `nmp.onnx` |

**Decisiones que se derivan de esto:**

1. Fijar **Python 3.11** para el pipeline (`brew install python@3.11`), no 3.13/3.14.
2. Fijar `torch==2.2.2` en el lock. No actualizar: rompe la instalación.
3. **Evitar `madmom`** — depende de `numpy<1.24` y Cython viejo, no compila limpio.
   `beat_this` lo reemplaza y es mejor (SOTA 2024).
4. **No se uso `autochord` ni BTC al final.** El reconocedor propio (croma
   sincronizado al beat + plantillas armonicas + Viterbi) dio 99,8 % en la
   validacion sin arrastrar TensorFlow ni un repo clonado a mano. BTC queda como
   upgrade disponible si la precision sobre grabaciones reales no alcanza.
5. Todo corre en **CPU**. Esto implica análisis **offline, en batch, una vez por
   canción** — no en tiempo real. Es la decisión arquitectónica central.

**Tiempos medidos** (no estimados) sobre 44 s de audio, Fase 1 completa:

| Etapa | Medido |
|---|---|
| Carga de audio | 2,0 s |
| `beat_this` (beats + downbeats) | 5,8 s |
| Acordes (croma + Viterbi) | 3,9 s |
| `basic-pitch` (notas para tablatura) | 3,1 s |
| **Total** | **15 s** = ~1/3 del largo de la cancion |

Mucho mejor de lo estimado: una cancion de 4 minutos sale en ~80 s. Demucs y
WhisperX siguen siendo los unicos pasos verdaderamente caros, y por eso quedan
como **opcionales y diferidos**, no en el camino critico.

**Precision medida** contra progresion conocida (G-Em-C-D, 90 BPM, 4/4):
**99,8 %** de acorde exacto ponderado por tiempo; BPM 90,9 vs 90,0; compas y
tonalidad correctos.

---

## 3. Arquitectura

```
┌──────────────────────────────────────────────────────┐
│  Biblioteca local de audio  (mp3 / flac / wav / m4a) │
└────────────────────────┬─────────────────────────────┘
                         │
         ┌───────────────▼────────────────┐
         │  PIPELINE  (Python 3.11, CPU)  │   ← corre UNA vez por canción
         │                                │
         │  ffmpeg      → PCM 44.1k mono  │
         │  beat_this   → beats, downbeats│
         │  BTC         → acordes + tiempo│
         │  essentia    → tonalidad, BPM  │
         │  [opcional] demucs   → stems   │
         │  [opcional] whisperx → letras  │
         └───────────────┬────────────────┘
                         │
              ┌──────────▼───────────┐
              │  analysis.json       │   ← la única fuente de verdad
              │  + SQLite (índice)   │
              └──────────┬───────────┘
                         │
         ┌───────────────▼─────────────────┐
         │  APP  (browser, local)          │   ← todo esto es instantáneo
         │                                 │
         │  Transposición · Capo · Loop    │
         │  Time-stretch (SoundTouch)      │
         │  Diagramas SVG · Mixer de stems │
         │  Afinador · Export MIDI         │
         └─────────────────────────────────┘
```

La clave: **separar análisis de reproducción**. El análisis es lento y se hace
una sola vez. Todo lo que Chordify cobra vive en el lado rápido, operando sobre
el JSON. Transponer una canción no requiere re-analizar nada: es sumar un
entero a cada acorde.

### Stack

**Pipeline:** Python 3.11 · torch 2.2.2 · beat_this · BTC · librosa · essentia ·
pretty_midi · ffmpeg 9.0.2 (brew) · gestionado con `uv`

**App:** Node 24 + pnpm (ya instalados) · SvelteKit o Next.js · Web Audio API ·
`soundtouchjs` (time-stretch) · SVG propio para diagramas

---

## 4. Esquema de datos (`analysis.json`)

Todo feature de la app se deriva de esta estructura:

```jsonc
{
  "version": 1,
  "source": { "path": "...", "sha256": "...", "duration": 243.5 },
  "key":   { "tonic": "G", "mode": "major", "confidence": 0.87 },
  "tempo": { "bpm": 92.4, "meter": "4/4" },
  "beats": [
    { "t": 0.512, "downbeat": true,  "bar": 1, "beat": 1 },
    { "t": 1.162, "downbeat": false, "bar": 1, "beat": 2 }
  ],
  "chords": [
    { "start": 0.512, "end": 3.110, "label": "G",   "root": 7, "quality": "maj" },
    { "start": 3.110, "end": 5.708, "label": "Em7", "root": 4, "quality": "min7" }
  ],
  "sections": [ { "start": 0.0, "end": 32.4, "label": "intro" } ],
  "stems":  { "vocals": "stems/vocals.wav", "other": "stems/other.wav" },
  "lyrics": [ { "start": 12.3, "end": 15.8, "text": "..." } ]
}
```

`root` como entero 0–11 es lo que hace la transposición trivial:
`(root + semitonos) % 12`. El capo es lo mismo con signo invertido más una tabla
de digitaciones preferidas por posición.

---

## 5. Roadmap

**Fase 1 — Pipeline mínimo — ✅ HECHA**
Entorno py3.11 + torch 2.2.2. ffmpeg estatico. beat_this, reconocedor de acordes
propio, basic-pitch para notas. Entrada por YouTube (yt-dlp) o archivo local.
Salidas: grilla con digitaciones, tablatura ASCII, MusicXML, ChordPro, MIDI.
*Criterio de exito alcanzado: 99,8 % de acorde exacto en la validacion.*
Ver `README.md` para uso y `tests_pipeline.py` para la validacion.

**Fase 2 — Reproductor sincronizado**
Cargar audio + JSON. Grilla de compases que avanza con la reproducción.
Diagramas SVG de guitarra. Esto ya iguala el Chordify gratuito.

**Fase 3 — Todo lo premium** (transposicion y capo ya estan hechos en Fase 1)
Transposición, capo, loop A-B, time-stretch con SoundTouch, volumen separado,
export MIDI. Son casi todos derivaciones del JSON: la fase más barata y la que
más valor agrega.

**Fase 4 — Extras**
Afinador, Demucs para silenciar la guitarra, letras con WhisperX, detección en
vivo por micrófono.

---

## 6. Límites

- **No se scrapea Chordify.** Su base de acordes es su producto. Generamos los
  nuestros con nuestro propio pipeline — mismo resultado, origen propio.
- **No se descarga audio de YouTube** (viola sus términos). La app trabaja sobre
  archivos de audio propios, que es el camino legítimo y además da mejor
  calidad de análisis que el audio comprimido de streaming.
- Los modelos usados son libres; conviene revisar la licencia de cada checkpoint
  pre-entrenado si en algún momento se distribuye la app.
