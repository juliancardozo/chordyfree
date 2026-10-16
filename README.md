# ChordLocal

Pegás un video de YouTube (o un archivo de audio) y obtenés una **hoja con la
letra y los acordes, sincronizada con el audio**, más un **análisis musical**
de la canción. Además genera grilla con digitaciones, tablatura, partitura y
MIDI. El análisis del audio corre local, en CPU.

## Empezar

Requisitos: macOS o Linux, [uv](https://docs.astral.sh/uv/) y conexión a
internet la primera vez. No hace falta GPU ni instalar ffmpeg (viene incluido).

```bash
# 1. Instalar uv (si no lo tenés)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clonar e instalar (uv baja Python 3.11 solo si hace falta)
git clone https://github.com/juliancardozo/chordyfree.git
cd chordyfree
uv venv --python 3.11 .venv
uv pip install -p .venv/bin/python -e .

# 3. Abrir la app
.venv/bin/chordlocal app
```

Se abre `http://127.0.0.1:7700`. Pegá una URL de YouTube y apretá **Analizar**.
El primer análisis tarda un poco más: baja el modelo de beats (~80 MB, una sola
vez). Una canción de 4 minutos tarda ~1,5 min en total, búsqueda online incluida.

Si algo falla al instalar en una Mac Intel, ver la sección
[Instalación](#instalación-y-entorno): los pines de versiones son a propósito.

## Interfaz gráfica

```bash
.venv/bin/chordlocal app
```

Abre `http://127.0.0.1:7700`. Pegás la URL, apretás **Analizar**, y al terminar
se abre la canción. Corre solo en localhost.

La hoja (`cancion.html`) tiene:

| | |
|---|---|
| **Reproductor** | el acorde que suena y el que viene, en grande; línea de tiempo coloreada por acorde (clic para saltar); velocidad 0,5×–1,5× sin cambiar el tono |
| **Letra y acordes** | la línea que se canta se resalta y la hoja la sigue sola |
| **Transporte, cejilla, simplificar** | en vivo; "simplificar" baja todo a mayores y menores |
| **Diagramas** | clic en cualquier acorde; "▶ desde acá" salta el audio a ese punto |
| **Análisis** | tonalidad, ciclo principal en grados, patrones conocidos, cadencias, reparto funcional, acordes fuera de escala, dificultad y cejilla sugerida |
| **Fuentes** | qué se encontró online, cuánto concuerda cada transcripción con el audio, botón para elegir otra, re-buscar con otro nombre, o pegar una a mano |

## Cómo se arma la letra con acordes

Después de analizar el audio, se busca online, en este orden de prioridad:

1. **Una transcripción importada por vos** (pegada en la app o con el marcador).
2. **Cifra Club** — se intenta, pero hoy responde 403 a cualquier cliente que no
   sea un navegador. ChordLocal no se disfraza de navegador para sortearlo: deja
   el enlace a la cifra y el marcador **"Enviar a ChordLocal"** (en la app, abajo
   de todo), que la manda desde tu propio navegador.
3. **LaCuerda.net** — se buscan candidatas, se leen las mejores y **se elige la
   que más concuerda con lo que suena en el audio**, no solo la de título más
   parecido. Si la transcripción está en otro tono (típico: grabación afinada
   medio tono abajo) se detecta el desplazamiento y la hoja se muestra en el tono
   que suena, con el tono escrito a un clic.
4. **LRCLIB** — letras abiertas, muchas **sincronizadas** línea por línea.

Con esas piezas:

- **Transcripción + audio**: si concuerdan (≥ 55 %), los acordes del audio se
  re-deciden usando el vocabulario de la transcripción como guía. El audio sigue
  decidiendo *cuándo* cambia el acorde; la hoja desempata lecturas que el croma
  deja parejas (E vs G#m). Si además hay letra sincronizada, cada línea de la
  hoja recibe su tiempo y la hoja sigue al audio.
- **Solo letra sincronizada**: los acordes detectados en el audio se ubican
  sobre la palabra que se canta en ese momento. Los instrumentales van en línea propia.
- **Solo letra sin tiempos**: letra + grilla por compás que se resalta al sonar.
- **Nada**: la grilla por compás.

## Uso por línea de comandos

```bash
# Analizar + buscar letra y acordes online (todo de una)
.venv/bin/chordlocal analizar "https://www.youtube.com/watch?v=..."

# Sin tablatura (más rápido), o sin búsqueda online
.venv/bin/chordlocal analizar cancion.mp3 --no-notes
.venv/bin/chordlocal analizar cancion.mp3 --sin-buscar

# Re-buscar con otro nombre (si el título del video no ayuda)
.venv/bin/chordlocal buscar out/<slug>/analysis.json --tema "Las palabras" --artista "Jorge Drexler"

# Usar una transcripción tuya y regenerar la hoja
.venv/bin/chordlocal tocar out/<slug>/analysis.json --letra mi-cifra.txt

# Re-renderizar un análisis ya hecho (instantáneo)
.venv/bin/chordlocal mostrar out/<slug>/analysis.json --transponer -1
```

## Salidas

En `out/<slug>/`:

| Archivo | Qué es |
|---|---|
| `cancion.html` | **La hoja principal**: letra + acordes + reproductor + análisis + fuentes |
| `hoja.html` | Hoja analítica: versión ideal / simplificada, grados por compás |
| `acordes.txt` | Grilla de acordes por compás + diagramas de digitación |
| `tablatura.txt` | Tablatura ASCII, si se pidió |
| `partitura.musicxml` | Pentagrama + cifrado. Abrir en **MuseScore** |
| `cancion.chordpro` | ChordPro, lo lee cualquier app de canciones |
| `cancion.mid` | MIDI con pista de acordes (y de notas) |
| `fuentes.json` | Lo encontrado online: candidatas, concordancia con el audio, letra |
| `acordes-online.txt`, `letra.txt` | La transcripción y la letra elegidas |
| `acordes-importados.txt` | La transcripción que pegaste o enviaste vos |
| `analysis.json` | Fuente de verdad del análisis; de acá derivan las demás |

## Cómo funciona

```
audio ─┬─ ¿empieza otra pista al final? (fade + silencio + volumen pleno) → recortar
       ├─ beat_this → activaciones → compás (largo + pulsos) → beats por DP → downbeats por Viterbi
       ├─ croma por beat → plantillas + Viterbi → acordes (1a pasada)
       ├─ tonalidad + prior diatónico → re-decodificar (2a pasada)
       ├─ MusicBrainz / AcousticBrainz → contraste de tempo y tonalidad
       └─ basic-pitch → notas → tablatura (opcional)
                    │
     LaCuerda / Cifra Club / LRCLIB → concordancia con el audio → elegir fuente
                    │
       re-decodificar con el vocabulario de la hoja → letra con acordes y tiempos
```

### Mejoras de la versión 0.2, medidas

Contra cuatro transcripciones humanas de temas reales (rumba, murga, canción,
blues en vivo), mismo audio y mismo croma; cambian los pesos de las plantillas,
`change_penalty` (1,6 → 2,0) y la grilla de beats:

| | antes | ahora |
|---|---|---|
| Precisión de transiciones (cambios de acorde que existen en la transcripción) | 0,44 | **0,74** |
| Tiempo sonado con acordes del vocabulario de la transcripción | 0,78 | **0,96** |
| Concordancia de "Para hacer música…" con su transcripción de LaCuerda | 58,6 % | **98,2 %** |

El cambio principal fue el peso de la quinta en las plantillas (0,55 → 1,0): con
la quinta débil, una tríada perdía contra la tríada una tercera arriba, que lee
su quinta como tercera propia (E → G#m, Eb → Gm, A → C#m).

Además:
- **Compases coherentes**: antes beat_this sin post-procesado daba compases de 1
  a 12 beats; ahora el compás se ancla en la periodicidad del downbeat y todos los
  compases tienen la misma cantidad de pulsos (4/4, 3/4 o 6/8).
- **Recorte de la pista siguiente**: algunas descargas traen los primeros segundos
  del tema que sigue; se detecta y no se analiza.
- **URLs de YouTube normalizadas**: se quitan `&list=RD…`, `&t=` y demás.
- **Metadata musical**: se usan los campos `track`/`artist` que da YouTube para
  buscar, en vez del nombre del canal.
- **Armaduras con bemoles**: Eb mayor se escribe Eb, no D#.

## Rendimiento medido

En MacBook Pro Intel i7-6820HQ (2016), sin GPU, sobre 44 s de audio: ~15 s de
análisis (alrededor de 1/3 de la duración). Una canción de 4 minutos tarda
~80 s, más la búsqueda online (LaCuerda pide 5 s entre páginas: 10–25 s).

## Tests

```bash
.venv/bin/python tests_theory.py      # instantáneo: invariantes de teoría
.venv/bin/python tests_cancionero.py  # instantáneo: parser de cifras, ranking
.venv/bin/python tests_fuentes.py     # instantáneo: fuentes online, fusión, beats (sin red)
.venv/bin/python tests_pipeline.py    # ~30 s: audio sintético G–Em–C–D, 99,8 %
```

## Ajustar la detección

En `src/chordlocal/recognize.py`: `DEGREE_WEIGHT` (pesos de las notas en las
plantillas), `change_penalty` (2.0, costo de cambiar de acorde), `QUALITY_PRIOR`
(favorece tríadas sobre séptimas) y `VOCAB_QUALITIES`. En `src/chordlocal/beats.py`:
`METROS` y el prior hacia 4/4. En `pagina.py`: `UMBRAL_REFINAR`, la concordancia
mínima para dejar que la transcripción guíe a los acordes.

## Instalación y entorno

Python **3.11** es obligatorio (`requires-python = ">=3.11,<3.12"`). Los pines de
`pyproject.toml` no son arbitrarios: `torch==2.2.2` es el último wheel para Mac
Intel, y `numba`/`llvmlite`/`setuptools` están fijados para que compilen en
esa plataforma. Detalle en `docs/analisis-chordify.md`.

Las carpetas `cache/` (audio descargado) y `out/` (análisis, letras y
transcripciones) se crean solas y no se publican.

## Límites

- El **análisis del audio** no sale de tu máquina.
- La búsqueda online consulta LaCuerda.net (respetando el `Crawl-delay: 5` de su
  robots.txt), LRCLIB (API pública) y MusicBrainz. Todo se cachea en `cache/`.
- Las letras y transcripciones son obra de sus autores y de quienes las
  transcribieron; LaCuerda las publica "para exclusivo uso privado". La hoja es
  para uso personal y `out/` está en `.gitignore`: no se redistribuyen.
- Descargar audio de YouTube va contra sus términos de servicio; la decisión y la
  responsabilidad de usar esa entrada son de quien corre la herramienta.
- La transcripción de notas (tablatura) es aproximada en mezclas densas.
