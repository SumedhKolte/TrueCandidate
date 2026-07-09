"""
Speaker Diarization Cross-Check.

The fraud pattern: audio says participant X is talking (diarization attributes
speaking time to them), but computer vision on their video tile sees no lip
movement. That means the voice is coming from somewhere else — a hidden
coach, a virtual audio cable, or a deepfaked camera loop.

Implementation: the platform adapter periodically posts `lip_movement_sample`
events (score in [0,1] from a CV model; simulated in this prototype). Between
two samples we compare (Δ speaking_ms attributed by diarization) against the
lip score. Speech attributed + dead lips => anomaly signal.
"""
from __future__ import annotations

import logging

from .. import db, state
from ..signals import weight_of

log = logging.getLogger("truecandidate.diarization")

_MIN_SPEECH_DELTA_MS = 4000   # need meaningful attributed speech in the window
_DEAD_LIPS_THRESHOLD = 0.15   # below this the mouth is effectively static


async def check_lip_sample(
    ss: state.SessionState, ps: state.ParticipantState, lip_score: float
) -> None:
    speech_delta = ps.speaking_ms - ps.speaking_ms_at_last_lip_check
    ps.speaking_ms_at_last_lip_check = ps.speaking_ms
    ps.last_lip_score = lip_score

    if (
        ps.webcam_on                      # only meaningful when we can see them
        and speech_delta >= _MIN_SPEECH_DELTA_MS
        and lip_score < _DEAD_LIPS_THRESHOLD
    ):
        await db.emit_signal(
            ss.session_id, ps.participant_id, "lip_sync_anomaly",
            weight_of("lip_sync_anomaly"),
            payload={
                "attributed_speech_ms": speech_delta,
                "lip_movement_score": lip_score,
                "note": "voice attributed to participant but lips not moving",
            },
            source="diarization",
        )
