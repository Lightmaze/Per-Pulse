"""Causal change cues, short-lived evidence and query-anchored recall.

Low-level changes are candidates, not semantic scene boundaries. No trained
recognizer, emotion label, OCR, ASR or model inference is hidden in this module.
Capture and retention rates are independent of model requests and speech.
"""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import math
import struct
import time
import zlib
from typing import Any

import numpy as np

WIDTH, HEIGHT = 96, 54


class LifeError(ValueError):
    def __init__(self, code: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(code)


def number(value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise LifeError('invalid_numeric_range')
    return float(value)


def decode64(value: Any, maximum: int) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
        raise LifeError('media_size_exceeded', 413)
    try:
        result = base64.b64decode(value, validate=True)
    except ValueError:
        raise LifeError('invalid_media_encoding') from None
    if len(result) > maximum:
        raise LifeError('media_size_exceeded', 413)
    return result


def png(rgb: bytes) -> bytes:
    """Small valid RGB PNG, without loading an image library on the runtime."""
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    raw = b''.join(b'\0' + rgb[i:i + WIDTH * 3] for i in range(0, len(rgb), WIDTH * 3))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', WIDTH, HEIGHT, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 1)) + chunk(b'IEND', b'')


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    """Bound provider-side image size before passing through a browser JPEG."""
    if not data.startswith(b'\xff\xd8'):
        raise LifeError('jpeg_required')
    i = 2
    while i + 4 <= len(data):
        if data[i] != 255:
            raise LifeError('invalid_jpeg')
        while i < len(data) and data[i] == 255:
            i += 1
        if i >= len(data): break
        marker = data[i]; i += 1
        if marker in (0xD8, 0xD9): continue
        length = int.from_bytes(data[i:i + 2], 'big')
        if length < 2 or i + length > len(data): break
        if marker in (0xC0, 0xC1, 0xC2):
            if length < 8: break
            height, width = struct.unpack('!HH', data[i + 3:i + 7])
            if not 1 <= width <= 960 or not 1 <= height <= 960:
                raise LifeError('image_dimensions_exceeded', 413)
            return width, height
        i += length
    raise LifeError('invalid_jpeg')


class ChangeDetector:
    """Past-only robust baseline plus translation compensation and local ROI.

    Adaptation never requires future frames. Candidate confirmation uses one
    subsequent observed frame, labeling a return to the old view as transient.
    """
    def __init__(self):
        self.previous = None
        self.history = deque(maxlen=48)
        self.pending = None

    def step(self, rgb: bytes) -> tuple[dict, tuple[str, ...]]:
        frame = np.frombuffer(rgb, np.uint8).reshape(HEIGHT, WIDTH, 3).astype(np.float32) / 255
        gray = frame.mean(axis=2)
        histogram = np.concatenate([np.histogram(frame[..., c], bins=8, range=(0, 1))[0] for c in range(3)]).astype(float)
        histogram /= histogram.sum()
        cue = dict(delta=0., residual=0., histogram=0., roi=0., surprise=0., score=0.)
        labels: list[str] = []
        if self.previous is not None:
            previous, previous_hist = self.previous
            delta = float(np.mean(np.abs(gray - previous)))
            # Small pan/exposure changes are not automatically story changes.
            old = previous[3:-3, 3:-3]; current = gray[3:-3, 3:-3]
            exposure = float(np.median(current - old))
            residual = min(float(np.mean(np.abs(current - previous[3 + y:HEIGHT - 3 + y, 3 + x:WIDTH - 3 + x] - exposure)))
                           for x, y in ((0, 0), (-2, 0), (2, 0), (0, -2), (0, 2)))
            hist = float(np.abs(histogram - previous_hist).sum() / 2)
            d = np.abs(gray - previous)
            blocks = d.reshape(6, 9, 8, 12).mean(axis=(1, 3))
            local = float(np.quantile(blocks, .94))
            roi = float(d[40:].mean())
            baseline = np.asarray(self.history, dtype=float)
            median = float(np.median(baseline)) if len(baseline) else 0.
            mad = float(np.median(np.abs(baseline - median))) if len(baseline) else 0.
            surprise = max(0., (residual - median) / max(.015, 1.4826 * mad))
            score = min(1., max(hist, residual * 2, local * .8, roi * .8))
            if delta > .055 and (hist > .16 or residual > .11) and (surprise > 3 or hist > .35):
                labels.append('visual_transition_candidate')
            elif local > .065 and local > delta * 1.7 and surprise > 2:
                labels.append('local_change_candidate')
            if roi > .025 and roi > float(d[:40].mean()) * 2.2:
                labels.append('lower_region_change')  # Not an OCR/subtitle claim.
            if delta > .055 and residual < delta * .5:
                labels.append('camera_or_global_change')
            cue = dict(delta=delta, residual=residual, histogram=hist, roi=roi, surprise=surprise, score=score)
            self.history.append(residual)
        self.previous = (gray, histogram)
        return cue, tuple(labels)


@dataclass
class Frame:
    id: str
    t: float
    media_t: float | None
    epoch: int
    data: bytes
    mime: str
    score: float
    cues: tuple[str, ...]
    received: float
    signature: str

    def meta(self) -> dict:
        return dict(frame_id=self.id, capture_t=self.t, media_t=self.media_t, epoch=self.epoch,
                    score=round(self.score, 4), cues=self.cues, sha256=self.signature, mime=self.mime)


@dataclass
class Audio:
    t: float
    duration: float
    data: bytes
    epoch: int
    received: float
    rms: float


class LifeStream:
    """Finite recent evidence, never an implicit full recording archive."""
    def __init__(self, *, seconds=60., max_bytes=32 * 1024 * 1024, max_frames=1800):
        self.seconds, self.max_bytes, self.max_frames = seconds, max_bytes, max_frames
        self.frames: deque[Frame] = deque()
        self.audio: deque[Audio] = deque()
        self.events: deque[dict] = deque(maxlen=512)
        self.detector = ChangeDetector()
        self.bytes = 0
        self.last_t = -1.
        self.last_epoch = 0
        self.last_media = None
        self.count = 0
        self.evicted = 0
        self.pending = None
        self.last_score_t = -10.
        self.clock_gaps = 0

    def prune(self, now: float) -> None:
        while self.audio and now - self.audio[0].received > self.seconds:
            self.bytes -= len(self.audio.popleft().data)
        while self.frames and (now - self.frames[0].received > self.seconds
                or self.bytes > self.max_bytes or len(self.frames) > self.max_frames):
            self.bytes -= len(self.frames.popleft().data); self.evicted += 1
        while self.audio and self.bytes > self.max_bytes:
            self.bytes -= len(self.audio.popleft().data)
        # Event metadata has the same time-retention policy as evidence.
        while self.events and now - self.events[0]['received'] > self.seconds:
            self.events.popleft()

    def push_frame(self, t: float, rgb: bytes, *, media_t=None, epoch=0, jpeg=None,
                   now: float | None = None) -> Frame:
        now = time.monotonic() if now is None else now
        number(t, 0, 1e9)
        if len(rgb) != WIDTH * HEIGHT * 3 or t <= self.last_t:
            raise LifeError('frame_order_or_shape_invalid', 409)
        if type(epoch) is not int or epoch < self.last_epoch:
            raise LifeError('stale_source_epoch', 409)
        if media_t is not None: number(media_t, 0, 1e9)
        if epoch != self.last_epoch or (media_t is not None and self.last_media is not None and media_t + .2 < self.last_media):
            self.detector = ChangeDetector(); self.pending = None
            self.events.append(dict(type='playhead_discontinuity', t=t, epoch=epoch, received=now))
        if self.last_t >= 0 and t - self.last_t > .35:
            self.clock_gaps += 1
            self.events.append(dict(type='capture_gap', t=t, seconds=t - self.last_t, epoch=epoch, received=now))
        cue, reasons = self.detector.step(rgb)
        self.count += 1
        payload, mime = (jpeg, 'image/jpeg') if jpeg is not None else (png(rgb), 'image/png')
        if jpeg is not None: jpeg_dimensions(jpeg)
        item = Frame(f'f{self.count}', t, media_t, epoch, payload, mime, cue['score'], reasons, now, hashlib.sha256(payload).hexdigest())
        # Keep a candidate's before/after relationship, but never call a cut a
        # narrative boundary. One following sample distinguishes brief flashes.
        if self.pending is not None:
            old_id, middle, old_rgb = self.pending
            reverts = np.mean(np.abs(np.frombuffer(rgb, np.uint8).astype(float) - np.frombuffer(old_rgb, np.uint8))) / 255 < .035
            self.events.append(dict(type='transient_change' if reverts else 'visual_transition', t=middle.t,
                epoch=middle.epoch, frame_ids=[old_id, middle.id, item.id], score=middle.score, received=now))
            self.pending = None
        if 'visual_transition_candidate' in reasons and self.frames:
            self.pending = (self.frames[-1].id, item, getattr(self, '_previous_rgb', rgb))
        elif reasons:
            self.events.append(dict(type=reasons[0], t=t, epoch=epoch, frame_ids=[item.id], score=item.score, received=now))
        self._previous_rgb = rgb
        self.frames.append(item); self.bytes += len(payload)
        self.last_t, self.last_epoch, self.last_media = t, epoch, media_t
        self.prune(now)
        return item

    def push_audio(self, t: float, pcm: bytes, *, epoch: int, now=None) -> None:
        now = time.monotonic() if now is None else now
        number(t, 0, 1e9)
        if not pcm or len(pcm) > 32000 or len(pcm) % 2:
            raise LifeError('audio_shape_invalid')
        duration = len(pcm) / 32000.
        if self.audio and t < self.audio[-1].t + self.audio[-1].duration - .02:
            raise LifeError('audio_overlap', 409)
        values = np.frombuffer(pcm, '<i2').astype(float) / 32768
        rms = float(np.sqrt(np.mean(values ** 2)))
        prior = self.audio[-1].rms if self.audio else 0.
        if rms > .025 and rms > max(.004, prior) * 2.5:
            self.events.append(dict(type='audio_energy_change', t=t, epoch=epoch, rms=round(rms, 5), received=now))
        self.audio.append(Audio(t, duration, pcm, epoch, now, rms)); self.bytes += len(pcm)
        self.prune(now)

    def window(self, *, end: float, seconds=10., limit=8, now=None) -> tuple[list[Frame], dict]:
        now = time.monotonic() if now is None else now
        self.prune(now)
        number(end, 0, 1e9); number(seconds, .1, self.seconds)
        if type(limit) is not int or not 1 <= limit <= 24: raise LifeError('frame_budget_invalid')
        candidates = [f for f in self.frames if end - seconds <= f.t <= end]
        if not candidates: raise LifeError('evidence_expired_or_not_observed', 410)
        # Temporal coverage plus salient transitions. Select real frames, not
        # interpolated images. Quotas stop action scenes consuming all slots.
        chosen = {len(candidates) - 1}
        if limit > 1: chosen.add(0)
        if limit > 4: chosen.add(len(candidates) // 2)
        ranked = sorted(range(len(candidates)), key=lambda i: candidates[i].score, reverse=True)
        for index in ranked:
            if len(chosen) >= limit: break
            if all(abs(candidates[index].t - candidates[j].t) >= .20 for j in chosen):
                chosen.add(index)
        for index in np.linspace(0, len(candidates) - 1, min(limit, len(candidates)), dtype=int):
            if len(chosen) >= limit: break
            chosen.add(int(index))
        selected = [candidates[i] for i in sorted(chosen)]
        gaps = [e for e in self.events if end - seconds <= e['t'] <= end and e['type'] in ('capture_gap', 'playhead_discontinuity')]
        summary = dict(start=max(0, end - seconds), end=end, observed_start=candidates[0].t,
            observed_end=candidates[-1].t, captured_frames=len(candidates), selected_frames=len(selected),
            gap_count=len(gaps), expired_before=self.frames[0].t if self.evicted else None,
            events=[{k: v for k, v in e.items() if k != 'received'} for e in self.events if end - seconds <= e['t'] <= end][-40:],
            audio_available=any(end - seconds <= a.t < end for a in self.audio),
            semantic_interpretation='not_performed', selection='coverage_plus_visual_novelty',
            frame_sampling_gaps_possible=True)
        return selected, summary

    def audio_window(self, start: float, end: float) -> tuple[bytes, dict]:
        if not 0 < end - start <= 60: raise LifeError('audio_range_invalid')
        samples = int((end - start) * 16000)
        output = np.zeros(samples, dtype='<i2'); coverage = np.zeros(samples, dtype=bool)
        for chunk in self.audio:
            left, right = max(start, chunk.t), min(end, chunk.t + chunk.duration)
            if left >= right: continue
            offset = max(0, int((left - start) * 16000)); src = max(0, int((left - chunk.t) * 16000))
            data = np.frombuffer(chunk.data, '<i2')[src:src + int((right - left) * 16000)]
            data = data[:samples - offset]
            output[offset:offset + len(data)] = data; coverage[offset:offset + len(data)] = True
        pcm = output.tobytes()
        # Missing spans are explicit silence in preview, never 'heard' evidence.
        header = b'RIFF' + struct.pack('<I', 36 + len(pcm)) + b'WAVEfmt ' + struct.pack('<IHHIIHH',16,1,1,16000,32000,2,16) + b'data' + struct.pack('<I',len(pcm))
        return header + pcm, dict(coverage_fraction=round(float(coverage.mean()),4), missing_spans_rendered_as_silence=True)
