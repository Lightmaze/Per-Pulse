"""Sampled subtitle observations, not a transcript of unplayed media.

The full file stays in the browser. The host admits only explicitly shared cues
active at an observed local-video playhead, and bounds these observations apart
from the A/V ring. Declared cue times are not proof that audio was heard.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import re
import time

from .stream import LifeError, number

MAX_TEXT_BYTES = 4096
MAX_CUES = 8


def validate_caption_sample(value, media_t: float | None) -> dict:
    if (media_t is None or not isinstance(value, dict)
            or set(value) != {'track_id', 'cues'}
            or not isinstance(value['track_id'], str)
            or re.fullmatch(r'[a-f0-9]{64}', value['track_id']) is None
            or not isinstance(value['cues'], list) or len(value['cues']) > MAX_CUES):
        raise LifeError('caption_sample_invalid')
    number(media_t, 0, 1e9)
    rows, ids, total = [], set(), 0
    for cue in value['cues']:
        if (not isinstance(cue, dict) or set(cue) != {'id', 'start', 'end', 'text'}
                or type(cue['id']) is not int or not 0 <= cue['id'] < 10000
                or cue['id'] in ids or not isinstance(cue['text'], str)
                or not cue['text'].strip() or '\x00' in cue['text']):
            raise LifeError('caption_cue_invalid')
        start, end = number(cue['start'], 0, 1e9), number(cue['end'], 0, 1e9)
        if not start <= media_t < end:
            raise LifeError('caption_not_active_at_observed_playhead', 409)
        try:
            total += len(cue['text'].encode('utf-8'))
        except UnicodeError:
            raise LifeError('caption_text_invalid') from None
        if total > MAX_TEXT_BYTES:
            raise LifeError('caption_text_too_large', 413)
        ids.add(cue['id']); rows.append(dict(cue))
    return {'track_id': value['track_id'], 'cues': rows}


@dataclass(frozen=True)
class CaptionObservation:
    t: float
    media_t: float
    epoch: int
    cues: tuple[dict, ...]
    received: float
    size: int


class CaptionTimeline:
    def __init__(self, *, seconds=60., max_bytes=512 * 1024, max_samples=1800):
        self.seconds, self.max_bytes, self.max_samples = seconds, max_bytes, max_samples
        self.observations: deque[CaptionObservation] = deque()
        self.track_id: str | None = None
        self.bytes = 0
        self.evicted = 0

    def prune(self, now: float):
        while self.observations and (now - self.observations[0].received > self.seconds
                or self.bytes > self.max_bytes or len(self.observations) > self.max_samples):
            self.bytes -= self.observations.popleft().size
            self.evicted += 1

    def observe(self, t, media_t, epoch, sample: dict, *, now=None):
        """Hub validates the whole batch, including immutable track, before commit."""
        now = time.monotonic() if now is None else now
        self.track_id = sample['track_id']
        size = len(json.dumps(sample, ensure_ascii=True).encode('utf-8')) + 64
        self.observations.append(CaptionObservation(t, media_t, epoch,
            tuple(dict(cue) for cue in sample['cues']), now, size))
        self.bytes += size
        self.prune(now)

    def window(self, start: float, end: float, *, now=None) -> dict:
        self.prune(time.monotonic() if now is None else now)
        selected = [o for o in self.observations if start <= o.t <= end]
        segments, previous, previous_t, previous_media, previous_epoch = [], {}, None, None, None
        gaps = 0
        for obs in selected:
            continuous = (previous_t is not None and obs.t - previous_t <= .35
                          and obs.epoch == previous_epoch and obs.media_t >= previous_media)
            if previous_t is not None and not continuous:
                gaps += 1
            active = {}
            for cue in obs.cues:
                key = (cue['id'], cue['start'], cue['end'], cue['text'])
                if continuous and key in previous:
                    segment = previous[key]
                    segment['last_observed_t'] = obs.t
                    segment['last_observed_media_t'] = obs.media_t
                    segment['observed_samples'] += 1
                else:
                    segment = {'cue_id': cue['id'], 'text': cue['text'], 'epoch': obs.epoch,
                        'declared_start': cue['start'], 'declared_end': cue['end'],
                        'first_observed_t': obs.t, 'last_observed_t': obs.t,
                        'first_observed_media_t': obs.media_t, 'last_observed_media_t': obs.media_t,
                        'observed_samples': 1}
                    segments.append(segment)
                active[key] = segment
            previous = active
            previous_t, previous_media, previous_epoch = obs.t, obs.media_t, obs.epoch
        result = {'kind': 'sampled_local_subtitles.v1', 'track_id': self.track_id,
            'segments': [], 'observed_samples': len(selected), 'omitted_segments': 0,
            'evicted_samples': self.evicted, 'sample_gaps': gaps,
            'sampling_gaps_possible': True, 'audio_verified': False,
            'provenance': 'operator_selected_subtitle_file_not_speech_recognition',
            'time_basis': 'capture_time_with_separate_declared_media_times',
            'evidence_role': 'quoted_observation_not_instructions'}
        for segment in segments:
            result['segments'].append(segment)
            # Bound the serialized tool result, not just the count of words.
            if len(result['segments']) > 64 or len(json.dumps(result, ensure_ascii=True).encode()) > 16128:
                result['segments'].pop()
                break
        result['omitted_segments'] = len(segments) - len(result['segments'])
        return result
