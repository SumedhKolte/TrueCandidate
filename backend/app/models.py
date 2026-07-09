"""Webhook payload contracts (what a Zoom/Meet/Teams bot adapter would send)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class TranscriptChunk(BaseModel):
    session_id: str
    platform_participant_id: str = Field(description="Speaker per diarization")
    display_name: str
    text: str
    started_at_ms: int
    duration_ms: int


class LaunchRequest(BaseModel):
    """UI request: 'start observing this meeting'."""
    meeting_url: str = Field(description="Google Meet / Zoom / Teams link")
    candidates: list[str] = Field(min_length=1, description="Expected candidate names")
    interviewers: list[str] = Field(default_factory=list)
    # False when the Chrome extension is the capture layer: create the session
    # and roster, but send no bot into the call.
    dispatch_bot: bool = True


class PlatformEvent(BaseModel):
    session_id: str
    platform_participant_id: str
    display_name: str
    event: Literal[
        "participant_joined",
        "participant_left",
        "webcam_on",
        "webcam_off",
        "screen_share_started",
        "screen_share_stopped",
        "lip_movement_sample",   # simulated CV signal: payload.score in [0,1]
    ]
    payload: dict[str, Any] = Field(default_factory=dict)
