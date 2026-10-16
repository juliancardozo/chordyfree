# ChordLocal

Análisis local de canciones: detecta acordes, beats y notas, y genera
**grilla de acordes con digitaciones, tablatura, partitura y MIDI**.
Acepta URLs de YouTube o archivos de audio locales. Todo corre offline, en CPU.

## Interfaz grafica

```bash
.venv/bin/chordlocal app
```

Abre `http://127.0.0.1:7700` en el navegador. Desde ahi podes pegar una URL o
una ruta de archivo, ver el progreso del analisis en vivo, y navegar la
biblioteca de canciones ya analizadas con acceso directo a cada hoja.

Corre solo en localhost y no expone nada hacia afuera.

## Uso por linea de comandos

```bash
# Un archivo local
.venv/bin/chordlocal analizar ~/Music/cancion.mp3

# Un video de YouTube
.venv/bin/chordlocal analizar "https://www.youtube.com/watch?v=..."

# Transponer 2 semitonos arriba
.venv/bin/chordlocal analizar cancion.mp3 --transponer 2

# Digitaciones como si hubiera cejilla en el traste 3
.venv/bin/chordlocal analizar cancion.mp3 --capo 3

# Más rápido: solo acordes, sin tablatura
.venv/bin/chordlocal analizar cancion.mp3 --no-notes

# Re-renderizar un análisis ya hecho (instantáneo, no re-analiza)
.venv/bin/chordlocal mostrar out/<slug>/analysis.json --transponer -1
```

## Salidas

En `out/<slug>/`:

| Archivo | Qué es |
|---|---|
| `acordes.txt` | Grilla de acordes por compás + diagramas de digitación |
| `tablatura.txt` | Tablatura ASCII de 6 líneas, por compás |
| `partitura.musicxml` | Pentagrama + cifrado. Abrir en **MuseScore** (gratis) |
| `cancion.chordpro` | Formato ChordPro, lo lee cualquier app de canciones |
| `cancion.mid` | MIDI con pista de acordes y pista de notas |
| `hoja.html` | **Hoja interactiva**: transposición y cejilla en vivo, versión ideal / simplificada, análisis armónico, diagramas SVG |
| `analysis.json` | Fuente de verdad del análisis; de acá derivan las demás |

## Cómo funciona

```
audio → beats (beat_this) → croma por beat → plantillas + Viterbi → acordes  (1a pasada)
                                                    ↓
                                        tonalidad + prior diatonico
                                                    ↓
                                           re-decodificar          (2a pasada)
                                                    ↓
                        contraste con MusicBrainz / AcousticBrainz
                                                    ↓
          funcion armonica, pedales, acordes sin 3a, version simplificada
                                                    ↓
      basic-pitch → notas → programacion dinamica → tablatura
```

La **2a pasada** es la que mas precision aporta y no cuesta nada: reusa el croma
ya calculado y vuelve a decidir con un prior que premia lo diatonico de la
tonalidad detectada. Medido contra una transcripcion humana de referencia, la
coherencia diatonica subio de 60,9 % a 92,7 % y la coincidencia de 78,6 % a 87,6 %.

El análisis es la parte lenta y corre **una vez por canción**; queda guardado en
`analysis.json`. Transponer, poner cejilla o re-renderizar opera sobre ese JSON y
es instantáneo — por eso `mostrar` no vuelve a analizar nada.

## Rendimiento medido

En MacBook Pro Intel i7-6820HQ (2016), sin GPU, sobre 44 s de audio:

| Etapa | Tiempo |
|---|---|
| Carga de audio | 2,0 s |
| Beats y compases | 5,8 s |
| Acordes | 3,9 s |
| Notas (tablatura) | 3,1 s |
| **Total** | **~15 s** |

Es decir, alrededor de **1/3 del tiempo de la canción**. Una canción de 4 minutos
se analiza en ~80 s.

## Precisión

Validada contra audio sintético con progresión conocida (G–Em–C–D, 90 BPM, 4/4):

- **99,8 %** de exactitud de acorde (fundamental + calidad), ponderada por tiempo
- BPM: 90,9 detectado vs 90,0 real
- Compás y tonalidad: correctos

Sobre grabaciones reales la precisión es menor (mezcla densa, distorsión, acordes
ambiguos). `tests_theory.py` verifica además 60+ invariantes exactas de teoría
musical: toda digitación generada suena solo notas del acorde, con la fundamental
presente, y las formas con cejilla suenan en la tonalidad correcta.

```bash
.venv/bin/python tests_theory.py      # instantáneo
.venv/bin/python tests_pipeline.py    # ~30 s, genera su propio audio de prueba
```

## Ajustar la detección

En `src/chordlocal/recognize.py`:

- `change_penalty` (1.6) — costo de cambiar de acorde. Subirlo da secuencias más
  estables; bajarlo, más reactivas.
- `QUALITY_PRIOR` — prior por calidad. Las triadas se favorecen sobre las
  séptimas a propósito: sin eso, `Cmaj7` (C-E-G-B) se come a `C` y a `Em` a la vez.
- `VOCAB_QUALITIES` — vocabulario de acordes. Agregar calidades no siempre mejora:
  cada una compite con las demás.

## Instalación en otra máquina

```bash
uv venv --python 3.11 .venv
uv pip install -p .venv/bin/python -e .
```

Los pines de `pyproject.toml` no son arbitrarios — ver `docs/analisis-chordify.md`,
sección de entorno.

## Límites

- Trabaja sobre **tu propio audio**. No consulta ni replica la base de datos de
  ningún servicio.
- Descargar audio de YouTube va contra los términos de servicio de YouTube; la
  decisión y la responsabilidad de usar esa entrada son de quien corre la herramienta.
- La transcripción de notas es aproximada: en mezclas densas inventa y omite notas.
  La grilla de acordes es bastante más confiable que la tablatura nota por nota.
