# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2026 迪儿 Dear
# Author: https://github.com/sht2574898282-afk
from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any


FCP_VERSIONS = tuple(f"1.{n}" for n in range(8, 15))
XMEML_VERSIONS = ("4", "5")


@dataclass
class Issue:
    code: str
    message: str
    severity: str = "warning"
    project: str = ""
    clip: str = ""
    time: str = ""
    location: str = ""
    choices: dict[str, str] = field(default_factory=dict)
    details: str = ""
    id: str = ""

    def __post_init__(self):
        if not self.id:
            value = "|".join((self.code, self.project, self.clip, self.time, self.location, self.message))
            self.id = hashlib.sha256(value.encode()).hexdigest()[:16]

    def unresolved(self, decisions: dict[str, str]) -> bool:
        if self.choices:
            action = decisions.get(self.id)
            return action not in self.choices or action == "stop"
        return self.severity == "error"


@dataclass
class ProjectInfo:
    id: str
    name: str
    fps: str = ""
    duration: str = "0s"
    clip_count: int = 0
    media_count: int = 0


@dataclass
class MediaReference:
    id: str
    uri: str
    resolved_path: str = ""
    exists: bool | None = None


@dataclass
class Document:
    path: Path
    xml_path: Path | None = None
    family: str = "unknown"
    version: str = ""
    tree: Any = None
    projects: list[ProjectInfo] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    media: list[MediaReference] = field(default_factory=list)
    input_hash: str = ""
    bundle_root: Path | None = None


@dataclass(frozen=True)
class Target:
    family: str = "fcpxml"
    version: str = "1.9"
    application: str = "通用"


@dataclass
class ConversionPlan:
    source: Document
    target: Target
    project_ids: list[str] | None = None
    issues: list[Issue] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)

    def blockers(self, decisions: dict[str, str] | None = None) -> list[Issue]:
        return [i for i in self.issues if i.unresolved(decisions or {})]


@dataclass
class ConversionResult:
    status: str
    output_path: str = ""
    report_json: str = ""
    report_html: str = ""
    issues: list[Issue] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    message: str = ""


def json_safe(value: Any) -> Any:
    if isinstance(value, Fraction):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value
