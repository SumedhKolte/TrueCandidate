"""
Passive Observer Filter — background sweep.

Anyone with ZERO speaking time AND webcam off for > 3 minutes is almost
certainly not the interviewee: silent lurkers are note-takers, recruiters,
shadowing engineers… or the fraud coach. We don't eject them; we just push
their candidate-probability down hard (-40) so the real contenders separate.

Runs as a periodic asyncio task rather than per-event: absence of events IS
the evidence here, so something has to wake up and notice the silence.
"""
from __future__ import annotations

import logging
import time

from .. import db, state
from ..config import get_settings
from ..signals import weight_of

log = logging.getLogger("truecandidate.passive")


async def sweep() -> None:
    threshold_s = get_settings().passive_observer_after_s
    now = time.monotonic()

    for ss in state.all_sessions():
        for ps in ss.participants.values():
            if ps.is_interviewer or ps.speaking_ms > 0 or ps.webcam_on:
                continue
            cam_dark_for = now - (ps.webcam_off_since or ps.joined_at)
            present_for = now - ps.joined_at
            if min(cam_dark_for, present_for) > threshold_s:
                # emit_signal() de-duplicates FIRE_ONCE types, so the sweep can
                # re-evaluate every cycle without stacking penalties.
                await db.emit_signal(
                    ss.session_id, ps.participant_id, "passive_observer",
                    weight_of("passive_observer"),
                    payload={"silent_for_s": int(present_for),
                             "webcam_off_for_s": int(cam_dark_for)},
                    source="passive_filter",
                )
