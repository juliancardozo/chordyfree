"""Beats, compas y downbeats a partir de las activaciones de beat_this.

beat_this da, por frame (50 por segundo), la probabilidad de beat y de
downbeat. Su post-procesado "minimal" solo elige picos, sin ninguna nocion de
tempo ni de compas, y eso falla de dos maneras medidas:

  * en temas con swing o shuffle se engancha a la subdivision ternaria
    (0,34 s en vez de 0,49 s) y reporta 176 BPM para un tema de 123;
  * marca downbeats sueltos, con compases de 1, 2, 5 o 12 beats, asi que la
    grilla por compas, la partitura y el MIDI salen desfasados.

El post-procesado oficial (DBN) depende de madmom, que no compila en este
entorno. Este modulo lo reemplaza con dos pasos clasicos:

  1. Tempo por autocorrelacion de la activacion de beat, con un prior suave
     hacia el rango en que se suele contar (~70-160 BPM), y seguimiento por
     programacion dinamica (Ellis 2007) que obliga a beats casi equiespaciados
     pero tolera que el tempo respire.
  2. Compas y fase con un Viterbi sobre "posicion dentro del compas": los
     estados avanzan de a uno y vuelven a 1 al completar 3 o 4 beats; la
     emision es la activacion de downbeat en cada beat. Cambiar de fase cuesta,
     asi que un downbeat espurio no rompe la grilla.
"""
from __future__ import annotations

import numpy as np

FPS = 50.0


def activations(y: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Probabilidad de beat y de downbeat por frame (50 fps)."""
    import torch
    from beat_this.inference import Audio2Frames

    a2f = Audio2Frames(device="cpu")
    with torch.no_grad():
        b, d = a2f(y, sr)
    return torch.sigmoid(b).numpy().astype(float), torch.sigmoid(d).numpy().astype(float)


def picos(act: np.ndarray, umbral: float = 0.5) -> np.ndarray:
    """Maximos locales sobre el umbral: el post-procesado 'minimal' de beat_this."""
    if len(act) < 3:
        return np.zeros(0)
    m = (act[1:-1] >= act[:-2]) & (act[1:-1] > act[2:]) & (act[1:-1] >= umbral)
    return (np.nonzero(m)[0] + 1) / FPS


def estimar_periodo(act: np.ndarray, bpm_min: float = 50, bpm_max: float = 220,
                    centro: float = 115.0) -> float:
    """Periodo de beat en frames, por autocorrelacion con prior log-gaussiano.

    El prior desempata entre niveles metricos (negra vs corchea) que la
    autocorrelacion sola deja casi iguales; no decide nada cuando un nivel
    domina claramente.
    """
    x = act - act.mean()
    n = len(x)
    if n < 4 * FPS:
        return FPS * 60 / centro
    spec = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    ac /= ac[0] + 1e-9

    lags = np.arange(int(FPS * 60 / bpm_max), int(FPS * 60 / bpm_min) + 1)
    lags = lags[lags < n]
    bpms = 60 * FPS / lags
    prior = np.exp(-0.5 * (np.log2(bpms / centro) / 0.9) ** 2)
    # Un periodo verdadero tambien correlaciona a su doble: sumarlo estabiliza
    # la eleccion frente a picos sueltos de subdivision.
    ac2 = np.array([ac[min(2 * l, n - 1)] for l in lags])
    score = (ac[lags] + 0.5 * ac2) * prior
    best = int(lags[int(np.argmax(score))])

    # Refinamiento parabolico para no quedar atado a la grilla de 20 ms.
    if 1 <= best < n - 1:
        a, b, c = ac[best - 1], ac[best], ac[best + 1]
        den = a - 2 * b + c
        if den < 0:
            return best + 0.5 * (a - c) / den
    return float(best)


def seguir(act: np.ndarray, periodo: float, rigidez: float = 120.0) -> np.ndarray:
    """Seguimiento por programacion dinamica: beats que maximizan la activacion
    total con intervalos cercanos al periodo. Devuelve tiempos en segundos."""
    n = len(act)
    if n == 0 or periodo <= 0:
        return np.zeros(0)
    local = act / (act.std() + 1e-9)
    score = local.copy()
    back = -np.ones(n, dtype=int)
    lo, hi = int(round(periodo * 0.5)), int(round(periodo * 2.0))
    offs = np.arange(lo, hi + 1)
    costo = -rigidez * np.log(offs / periodo) ** 2
    for t in range(lo, n):
        prev = t - offs
        ok = prev >= 0
        if not ok.any():
            continue
        cand = score[prev[ok]] + costo[ok]
        k = int(np.argmax(cand))
        if cand[k] > 0:
            score[t] = local[t] + cand[k]
            back[t] = prev[ok][k]

    # Arranca del mejor final dentro del ultimo periodo.
    cola = np.arange(max(0, n - int(periodo * 1.5)), n)
    t = int(cola[np.argmax(score[cola])])
    path = []
    while t >= 0:
        path.append(t)
        t = back[t]
    path = np.array(path[::-1])

    # Se recortan los beats de las puntas donde no suena nada (silencio inicial
    # o final): el DP los inventa para mantener el pulso.
    piso = 0.1 * np.percentile(act[path], 90) if len(path) else 0
    vals = np.array([act[max(0, p - 2):p + 3].max() for p in path])
    fuertes = np.nonzero(vals > piso)[0]
    if len(fuertes):
        path = path[fuertes[0]:fuertes[-1] + 1]
    return path / FPS


def compas(beats: np.ndarray, down_act: np.ndarray,
           metros: tuple[int, ...] = (3, 4),
           salto: float = 6.0) -> tuple[np.ndarray, int]:
    """Elige compas y ubica los downbeats sobre beats ya regulares.

    Viterbi sobre estados (metro, posicion). Avanzar es gratis, cambiar de
    metro es muy caro y saltar de fase cuesta `salto` en log. La emision es el
    log-odds de la activacion de downbeat en cada beat.
    """
    if len(beats) < 4:
        return beats[:1], 4
    idx = np.clip((beats * FPS).round().astype(int), 0, len(down_act) - 1)
    p = np.array([down_act[max(0, i - 2):i + 3].max() for i in idx])
    p = np.clip(p, 1e-4, 1 - 1e-4)
    si, no = np.log(p), np.log(1 - p)

    estados = [(m, k) for m in metros for k in range(m)]
    S = len(estados)
    emis = np.array([[si[t] if k == 0 else no[t] for (m, k) in estados]
                     for t in range(len(beats))])

    trans = np.full((S, S), -np.inf)
    for i, (m, k) in enumerate(estados):
        for j, (m2, k2) in enumerate(estados):
            if m2 == m and k2 == (k + 1) % m:
                trans[i, j] = 0.0
            elif m2 == m:
                trans[i, j] = -salto
            else:
                trans[i, j] = -4 * salto

    # Prior leve hacia 4/4, que es por lejos lo mas comun.
    inicio = np.array([0.0 if m == 4 else -1.0 for (m, k) in estados])
    delta = inicio + emis[0]
    back = np.zeros((len(beats), S), dtype=int)
    for t in range(1, len(beats)):
        cand = delta[:, None] + trans
        back[t] = np.argmax(cand, axis=0)
        delta = cand[back[t], np.arange(S)] + emis[t]
    s = int(np.argmax(delta))
    path = [s]
    for t in range(len(beats) - 1, 0, -1):
        s = back[t, s]
        path.append(s)
    path = path[::-1]

    metro = int(np.bincount([estados[s][0] for s in path]).argmax())
    downs = np.array([beats[t] for t, s in enumerate(path) if estados[s][1] == 0])
    return downs, metro


def regularidad(beats: np.ndarray) -> float:
    """Cociente p90/p10 de los intervalos: 1.0 es un pulso perfecto."""
    if len(beats) < 4:
        return 9.9
    iois = np.diff(beats)
    p10, p90 = np.percentile(iois, [10, 90])
    return float(p90 / max(p10, 1e-6))


def _acf(x: np.ndarray) -> np.ndarray:
    x = x - x.mean()
    n = len(x)
    spec = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(spec * np.conj(spec))[:n]
    return ac / (ac[0] + 1e-9)


def _en(ac: np.ndarray, lag: float) -> float:
    """Autocorrelacion interpolada en un lag fraccionario."""
    i = int(lag)
    if i + 1 >= len(ac) or i < 0:
        return 0.0
    f = lag - i
    return float(ac[i] * (1 - f) + ac[i + 1] * f)


# Pulsos por compas que se consideran, y como se escribe cada compas.
METROS = {4: "4/4", 3: "3/4", 6: "6/8"}


def elegir_metro(beat_act: np.ndarray, down_act: np.ndarray,
                 onset: np.ndarray | None = None, onset_fps: float = FPS
                 ) -> dict | None:
    """Duracion del compas y cuantos pulsos entran en el.

    El largo del compas sale de la periodicidad de la activacion de downbeat,
    que es la senal mas limpia que da la red (un pico neto por compas). Lo
    ambiguo es la subdivision: en un tema con shuffle la red puede ver 6
    pulsos donde los onsets ven 4. Se suman las dos evidencias para cada
    lectura posible. Devuelve None si el compas no es periodico.
    """
    ad = _acf(down_act)
    lags = np.arange(int(1.0 * FPS), int(4.5 * FPS))
    lags = lags[lags < len(ad) - 1]
    if not len(lags):
        return None
    k = int(np.argmax(ad[lags]))
    barra = float(lags[k])
    if ad[lags[k]] < 0.2:
        return None
    # Refinamiento parabolico del largo del compas.
    i = int(barra)
    a, b, c = ad[i - 1], ad[i], ad[i + 1]
    den = a - 2 * b + c
    if den < 0:
        barra = i + 0.5 * (a - c) / den

    ab = _acf(beat_act)
    ao = _acf(onset) if onset is not None and len(onset) > 10 else None
    puntajes = {}
    for m in METROS:
        pulso = barra / m
        if pulso / FPS < 0.22:         # mas rapido que ~270 BPM: no es un pulso
            continue
        sc = _en(ab, pulso)
        if ao is not None:
            sc += _en(ao, pulso * onset_fps / FPS)
        if m == 4:
            sc += 0.10                 # 4/4 es por lejos lo mas comun
        puntajes[m] = round(sc, 3)
    if not puntajes:
        return None
    m = max(puntajes, key=puntajes.get)
    return {"pulsos": m, "barra": barra, "pulso": barra / m, "puntajes": puntajes}


def track(y: np.ndarray, sr: int, onset: np.ndarray | None = None,
          onset_fps: float = FPS) -> dict:
    """Beats, downbeats y compas. Devuelve tambien las metricas de la decision.

    `onset` es la envolvente de onsets (opcional) a `onset_fps` frames/s; ayuda
    a desempatar la subdivision binaria de la ternaria.
    """
    beat_act, down_act = activations(y, sr)
    minimal = picos(beat_act)

    eleccion = elegir_metro(beat_act, down_act, onset, onset_fps)
    if eleccion:
        periodo = eleccion["pulso"]
        metros = (eleccion["pulsos"],)
    else:
        periodo = estimar_periodo(beat_act)
        metros = (3, 4)
    dp = seguir(beat_act, periodo)

    # Si los picos de la red ya forman un pulso estable y coinciden con el
    # tempo estimado, se usan tal cual: estan alineados al frame exacto y
    # siguen cualquier respiracion del tempo mejor que el DP.
    reg_min = regularidad(minimal)
    med_min = float(np.median(np.diff(minimal))) * FPS if len(minimal) > 2 else 0
    usa_minimal = bool(reg_min < 1.25 and med_min
                       and abs(med_min - periodo) / periodo < 0.06)
    beats = minimal if usa_minimal else dp
    if len(beats) < 4:
        beats = minimal if len(minimal) >= len(dp) else dp

    downs, pulsos = compas(beats, down_act, metros=metros)
    metro = METROS.get(pulsos, f"{pulsos}/4")
    # En 6/8 se cuenta la negra con puntillo (3 pulsos), no la corchea.
    por_tiempo = 3 if pulsos == 6 else 1
    bpm = 60 * FPS / (periodo * por_tiempo) if periodo else 0.0
    return {
        "beats": beats, "downbeats": downs, "metro": metro, "pulsos": pulsos,
        "bpm": round(bpm, 1),
        "metodo": "picos" if usa_minimal else "programacion dinamica",
        "regularidad_picos": round(reg_min, 2),
        "puntajes_metro": (eleccion or {}).get("puntajes", {}),
    }
