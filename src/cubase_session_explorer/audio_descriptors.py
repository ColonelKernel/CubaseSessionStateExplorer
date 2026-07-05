"""Baseline acoustic descriptors for state<->audio linkage.

Tiered so the state->audio demo runs with ZERO third-party deps (stdlib
``wave`` gives level, crest factor and zero-crossing rate — enough to measure
the DualFilter brightness delta), and upgrades automatically when numpy /
librosa / pyloudnorm are installed (spectral centroid/rolloff/bandwidth,
integrated LUFS).

These are deliberately modest, interpretable descriptors — baseline acoustic
summaries, NOT perceptual ground truth. We never oversell them.
"""

from __future__ import annotations

import math
import wave
from typing import Optional

from .ids import make_id
from .models import AudioDescriptorSet
from .utils import linear_to_db


def _load_wav_stdlib(path: str):
    with wave.open(path, "r") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        sw = w.getsampwidth()
        raw = w.readframes(n)
    if sw != 2:
        return None
    import array
    a = array.array("h")
    a.frombytes(raw)
    if ch > 1:
        mono = [sum(a[i:i + ch]) / ch for i in range(0, len(a), ch)]
    else:
        mono = list(a)
    mono = [x / 32768.0 for x in mono]
    return mono, sr, ch


def extract(path: str, source_id: str = "render", source_type: str = "mixdown") -> AudioDescriptorSet:
    d = AudioDescriptorSet(id=make_id("desc"), source_id=source_id,
                           source_type=source_type, file_path=path)
    try:
        _extract_rich(path, d) or _extract_stdlib(path, d)
    except Exception as exc:  # never crash the pipeline on an odd file
        d.available = False
        d.warnings.append(f"descriptor extraction failed: {exc}")
    return d


def _extract_rich(path: str, d: AudioDescriptorSet) -> bool:
    try:
        import numpy as np
    except ImportError:
        return False
    try:
        import soundfile as sf
        y, sr = sf.read(path, always_2d=True)
    except Exception:
        loaded = _load_wav_stdlib(path)
        if loaded is None:
            return False
        mono, sr, _ = loaded
        y = np.asarray(mono).reshape(-1, 1)
    mono = y.mean(axis=1)
    d.sample_rate = int(sr)
    d.duration_seconds = round(len(mono) / sr, 4)
    d.rms_mean = float(np.sqrt(np.mean(mono ** 2)))
    d.rms_std = float(np.std(np.sqrt(np.maximum(mono ** 2, 1e-12))))
    d.peak_amplitude = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if d.rms_mean and d.rms_mean > 0:
        d.crest_factor_db = round(20 * math.log10(max(d.peak_amplitude, 1e-9) / d.rms_mean), 2)
    d.zero_crossing_rate_mean = float(np.mean(np.abs(np.diff(np.sign(mono))) > 0))
    if y.shape[1] >= 2:
        L, R = y[:, 0], y[:, 1]
        denom = float(np.sqrt(np.mean(L ** 2)) * np.sqrt(np.mean(R ** 2))) or 1e-9
        corr = float(np.mean(L * R)) / denom
        d.stereo_width_proxy = round(1.0 - max(-1.0, min(1.0, corr)), 4)

    # spectral centroid / rolloff / bandwidth via magnitude FFT frames
    win = 2048
    hop = 1024
    if len(mono) >= win:
        freqs = np.fft.rfftfreq(win, 1.0 / sr)
        cents, rolls, bws = [], [], []
        for start in range(0, len(mono) - win, hop):
            frame = mono[start:start + win] * np.hanning(win)
            mag = np.abs(np.fft.rfft(frame))
            total = mag.sum()
            if total <= 1e-9:
                continue
            c = float((freqs * mag).sum() / total)
            cents.append(c)
            cumsum = np.cumsum(mag)
            roll_idx = int(np.searchsorted(cumsum, 0.85 * total))
            rolls.append(float(freqs[min(roll_idx, len(freqs) - 1)]))
            bws.append(float(np.sqrt(((freqs - c) ** 2 * mag).sum() / total)))
        if cents:
            d.spectral_centroid_mean = round(sum(cents) / len(cents), 2)
            d.spectral_rolloff_mean = round(sum(rolls) / len(rolls), 2)
            d.spectral_bandwidth_mean = round(sum(bws) / len(bws), 2)

    _loudness(path, d)
    d.available = True
    return True


def _extract_stdlib(path: str, d: AudioDescriptorSet) -> bool:
    loaded = _load_wav_stdlib(path)
    if loaded is None:
        d.available = False
        d.warnings.append("Only 16-bit WAV supported without numpy/soundfile.")
        return False
    mono, sr, ch = loaded
    n = len(mono)
    d.sample_rate = sr
    d.duration_seconds = round(n / sr, 4)
    d.peak_amplitude = max((abs(x) for x in mono), default=0.0)
    d.rms_mean = math.sqrt(sum(x * x for x in mono) / n) if n else 0.0
    if d.rms_mean > 0:
        d.crest_factor_db = round(20 * math.log10(max(d.peak_amplitude, 1e-9) / d.rms_mean), 2)
    # zero-crossing rate (brightness proxy — enough for the filter demo)
    zc = sum(1 for i in range(1, n) if (mono[i - 1] < 0) != (mono[i] < 0))
    d.zero_crossing_rate_mean = round(zc / n, 6) if n else 0.0
    d.warnings.append("numpy/soundfile not installed; spectral + LUFS omitted, "
                      "level + zero-crossing-rate computed from stdlib.")
    d.available = True
    return True


def _loudness(path: str, d: AudioDescriptorSet) -> None:
    try:
        import pyloudnorm as pyln
        import soundfile as sf
        data, rate = sf.read(path)
        meter = pyln.Meter(rate)
        d.integrated_loudness_lufs = round(float(meter.integrated_loudness(data)), 2)
    except Exception:
        pass


def descriptor_delta(a: AudioDescriptorSet, b: AudioDescriptorSet) -> dict:
    """Signed deltas of comparable descriptors (b - a)."""
    fields = ("rms_mean", "peak_amplitude", "crest_factor_db",
              "spectral_centroid_mean", "spectral_rolloff_mean",
              "spectral_bandwidth_mean", "zero_crossing_rate_mean",
              "stereo_width_proxy", "integrated_loudness_lufs", "duration_seconds")
    out = {}
    for f in fields:
        va, vb = getattr(a, f), getattr(b, f)
        if va is not None and vb is not None:
            out[f] = {"a": va, "b": vb, "delta": round(vb - va, 6)}
    return out
