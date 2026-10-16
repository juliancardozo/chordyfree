"""Obtencion de audio: URL de YouTube (u otro sitio soportado por yt-dlp) o archivo local.

El audio se normaliza siempre a WAV mono 44.1 kHz, que es lo que esperan los
modelos de analisis. Se cachea por ID/hash para no re-descargar ni re-decodificar.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

SAMPLE_RATE = 44100

URL_RE = re.compile(r"^https?://", re.I)


def ffmpeg_exe() -> str:
    """ffmpeg del sistema si existe; si no, el binario estatico de imageio-ffmpeg.

    En macOS Monterey Homebrew ya no publica bottles, asi que compilar ffmpeg es
    inviable. El binario bundleado evita todo eso.
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


@dataclass
class Source:
    """Audio listo para analizar, con su metadata."""
    wav: Path
    title: str
    artist: str = ""
    origin: str = ""        # URL o ruta original
    duration: float = 0.0
    video_id: str = ""
    track: str = ""         # nombre del tema segun la metadata musical de YouTube
    album: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["wav"] = str(self.wav)
        return d


def _slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip()
    s = re.sub(r"[\s_]+", "-", s)
    return s[:limit].strip("-").lower() or "track"


def _to_wav(src: Path, dest: Path) -> None:
    """Decodifica cualquier formato a WAV mono 44.1k PCM 16-bit."""
    cmd = [
        ffmpeg_exe(), "-y", "-loglevel", "error",
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16",
        str(dest),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _probe_duration(wav: Path) -> float:
    import soundfile as sf
    info = sf.info(str(wav))
    return float(info.frames) / float(info.samplerate)


_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([\w-]{11})")


def normalize_url(url: str) -> str:
    """Reduce un enlace de YouTube a `watch?v=<id>`.

    El boton de compartir de YouTube copia URLs de radio
    (`&list=RD...&start_radio=1`). Con esas, el audio descargado puede arrastrar
    los primeros segundos de la pista siguiente y el analizador los transcribe
    como parte de la cancion. Tambien se descarta `&t=`, que no aplica al audio.
    """
    if not re.search(r"youtube\.com|youtu\.be", url, re.I):
        return url
    m = _YT_ID.search(url)
    return f"https://www.youtube.com/watch?v={m.group(1)}" if m else url


def from_youtube(url: str, cache: Path) -> Source:
    """Descarga el audio de una URL con yt-dlp y lo convierte a WAV.

    Pide el mejor stream de solo-audio disponible para no gastar ancho de banda
    en video, y cachea por el ID del video.
    """
    import yt_dlp

    url = normalize_url(url)
    cache.mkdir(parents=True, exist_ok=True)

    # Primera pasada: solo metadata, para conocer el ID y poder cachear.
    # noplaylist es imprescindible: una URL con &list=RD... (radio automatica de
    # YouTube) hace que yt-dlp intente extraer la lista entera en vez del video.
    probe_opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                  "noplaylist": True}
    with yt_dlp.YoutubeDL(probe_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise RuntimeError("la URL no resolvio a ningun video")
        info = entries[0]

    vid = info.get("id") or hashlib.sha256(url.encode()).hexdigest()[:11]
    title = info.get("title") or "desconocido"
    # `artist`/`track` vienen cuando YouTube reconoce la musica (Content ID);
    # son mucho mas confiables que el nombre del canal para buscar letras.
    artist = (info.get("artist") or info.get("creator") or "").split(",")[0].strip()
    track = info.get("track") or ""
    album = info.get("album") or ""
    if not artist:
        artist = re.sub(r"\s*(- Topic|VEVO|Oficial|Official)\s*$", "",
                        info.get("uploader") or "", flags=re.I).strip()
    wav = cache / f"{vid}.wav"
    meta_path = cache / f"{vid}.source.json"

    if wav.exists() and meta_path.exists():
        meta = json.loads(meta_path.read_text())
        return Source(wav=wav, title=meta["title"], artist=meta.get("artist") or artist,
                      origin=url, duration=meta.get("duration", 0.0), video_id=vid,
                      track=meta.get("track") or track, album=meta.get("album") or album)

    tmpl = str(cache / f"{vid}.%(ext)s")
    # Sin postprocesadores a proposito: el FFmpegExtractAudio de yt-dlp exige
    # ffprobe ademas de ffmpeg, y el binario estatico de imageio-ffmpeg solo trae
    # ffmpeg. Descargamos el stream de audio crudo y lo convertimos nosotros con
    # _to_wav(), que es el mismo camino ya probado para archivos locales.
    opts = {
        "format": "bestaudio/best",
        "outtmpl": tmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    # yt-dlp deja el wav con el codec por defecto; lo re-normalizamos a mono 44.1k.
    produced = next((p for p in cache.glob(f"{vid}.*")
                     if p.suffix.lower() in (".wav", ".m4a", ".webm", ".opus", ".mp3")), None)
    if produced is None:
        raise RuntimeError(f"yt-dlp no dejo ningun archivo de audio para {vid}")

    if produced.resolve() != wav.resolve():
        _to_wav(produced, wav)
        produced.unlink(missing_ok=True)
    else:
        norm = cache / f"{vid}.norm.wav"
        _to_wav(wav, norm)
        norm.replace(wav)

    duration = _probe_duration(wav)
    meta_path.write_text(json.dumps(
        {"title": title, "artist": artist, "track": track, "album": album,
         "duration": duration, "origin": url}, ensure_ascii=False))
    return Source(wav=wav, title=title, artist=artist, origin=url,
                  duration=duration, video_id=vid, track=track, album=album)


def from_file(path: str | Path, cache: Path) -> Source:
    """Toma un archivo de audio local y lo normaliza a WAV mono 44.1k."""
    src = Path(path).expanduser()
    if not src.exists():
        raise FileNotFoundError(src)

    cache.mkdir(parents=True, exist_ok=True)
    # Hash de los primeros 8 MB: suficiente para identificar y rapido en discos lentos.
    h = hashlib.sha256()
    with src.open("rb") as fh:
        h.update(fh.read(8 * 1024 * 1024))
    key = f"{_slug(src.stem)}-{h.hexdigest()[:10]}"
    wav = cache / f"{key}.wav"

    if not wav.exists():
        _to_wav(src, wav)

    return Source(wav=wav, title=src.stem, origin=str(src),
                  duration=_probe_duration(wav), video_id=key)


def resolve(target: str, cache: Path) -> Source:
    """Punto de entrada unico: acepta URL o ruta local."""
    return from_youtube(target, cache) if URL_RE.match(target) else from_file(target, cache)
