import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from launchcheck.checks.base import Category, Severity, TargetType
from launchcheck.db.models import ScanStatus
from launchcheck.scoring import ScoreLabel
from launchcheck.security.url import MAX_URL_LENGTH


class WebScanRequest(BaseModel):
    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)


class ScanCreated(BaseModel):
    scan_id: uuid.UUID


class ErrorInfo(BaseModel):
    code: str
    message: str


class FindingOut(BaseModel):
    check_id: str
    severity: Severity
    category: Category
    title: str
    description: str
    why_it_matters: str
    fix_steps: list[str]
    fix_prompt: str
    evidence: dict[str, Any]


class PassedCheckOut(BaseModel):
    check_id: str
    category: Category
    title: str


class ScanReport(BaseModel):
    scan_id: uuid.UUID
    status: ScanStatus
    target_type: TargetType
    target_label: str
    phase: int
    score: int | None
    score_label: ScoreLabel | None
    category_scores: dict[str, int] | None
    meta: dict[str, Any]
    error: ErrorInfo | None
    findings: list[FindingOut]
    passed: list[PassedCheckOut]
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime
