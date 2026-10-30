"""Immutable evidence bundle for Court rulings (plan item 3.1, G07).

Evidence is never truncated and never decoded leniently. A file that is too
large, in an unsupported format, or not valid UTF-8 stays in the manifest with
an explicit status and no content; a content hash that does not match the
listing is an explicit error. HTTP transport lives in ``DeliverableFetcher``
(``ruling_orchestrator``); this module only classifies bytes and assembles the
bundle.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from service_commons.exceptions import ServiceError

EvidenceStatus = Literal[
    "included",
    "excluded_file_too_large",
    "excluded_total_budget",
    "unsupported_format",
    "undecodable",
]

_LISTING_TEXT_FIELDS = ("asset_id", "uploader_id", "filename", "content_type", "uploaded_at")


def _unavailable(message: str) -> ServiceError:
    return ServiceError("evidence_unavailable", message, 502, {})


@dataclass(frozen=True)
class EvidencePolicy:
    """Configured evidence limits and supported text formats."""

    max_total_bytes: int
    max_file_bytes: int
    allowed_extensions: frozenset[str]
    allowed_content_types: frozenset[str]

    def supports(self, filename: str, content_type: str) -> bool:
        """A file is supported when its extension or base media type is allowed."""
        extension = PurePosixPath(filename).suffix.lower()
        media_type = content_type.split(";", 1)[0].strip().lower()
        return extension in self.allowed_extensions or media_type in self.allowed_content_types


@dataclass(frozen=True)
class AssetListing:
    """One validated entry of Task Board's asset listing."""

    asset_id: str
    uploader_id: str
    filename: str
    content_type: str
    size_bytes: int
    content_hash: str
    uploaded_at: str


@dataclass(frozen=True)
class EvidenceFile:
    """One evidence file: listing metadata, verified hash, status and exact text."""

    asset_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    uploader_id: str
    uploaded_at: str
    status: EvidenceStatus
    text: str | None

    def manifest_entry(self, role: str) -> dict[str, Any]:
        """The JSON-ready manifest entry shown to judges."""
        return {
            "asset_id": self.asset_id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "uploader_id": self.uploader_id,
            "role": role,
            "uploaded_at": self.uploaded_at,
            "status": self.status,
            "content": self.text,
        }


def parse_asset_listing(listing: object) -> list[AssetListing]:
    """Validate Task Board's ``GET /tasks/{id}/assets`` body; malformed is an error."""
    assets = listing.get("assets") if isinstance(listing, dict) else None
    if not isinstance(assets, list):
        raise _unavailable("Task Board returned an invalid asset listing")
    parsed: list[AssetListing] = []
    for raw in assets:
        if not isinstance(raw, dict):
            raise _unavailable("Task Board returned an invalid asset listing")
        texts = [raw.get(field) for field in _LISTING_TEXT_FIELDS]
        size = raw.get("size_bytes")
        content_hash = raw.get("content_hash")
        if (
            not all(isinstance(value, str) and value != "" for value in texts)
            or type(size) is not int
            or size < 0
            or not isinstance(content_hash, str)
            or len(content_hash) != 64
        ):
            raise _unavailable("Task Board returned an invalid asset listing")
        parsed.append(
            AssetListing(
                asset_id=str(raw["asset_id"]),
                uploader_id=str(raw["uploader_id"]),
                filename=str(raw["filename"]),
                content_type=str(raw["content_type"]),
                size_bytes=size,
                content_hash=content_hash.lower(),
                uploaded_at=str(raw["uploaded_at"]),
            )
        )
    return parsed


class EvidenceScreen:
    """Classifies listed assets against the policy, tracking the total byte budget."""

    def __init__(self, policy: EvidencePolicy) -> None:
        self._policy = policy
        self._used_bytes = 0

    @staticmethod
    def _file(asset: AssetListing, status: EvidenceStatus, text: str | None) -> EvidenceFile:
        return EvidenceFile(
            asset_id=asset.asset_id,
            filename=asset.filename,
            content_type=asset.content_type,
            size_bytes=asset.size_bytes,
            sha256=asset.content_hash,
            uploader_id=asset.uploader_id,
            uploaded_at=asset.uploaded_at,
            status=status,
            text=text,
        )

    def exclusion(self, asset: AssetListing) -> EvidenceFile | None:
        """Return the excluded entry when ``asset`` must not be downloaded, else reserve it."""
        if not self._policy.supports(asset.filename, asset.content_type):
            return self._file(asset, "unsupported_format", None)
        if asset.size_bytes > self._policy.max_file_bytes:
            return self._file(asset, "excluded_file_too_large", None)
        if self._used_bytes + asset.size_bytes > self._policy.max_total_bytes:
            return self._file(asset, "excluded_total_budget", None)
        self._used_bytes += asset.size_bytes
        return None

    def admit(self, asset: AssetListing, content: bytes) -> EvidenceFile:
        """Verify the downloaded bytes against the listing and decode them strictly."""
        if hashlib.sha256(content).hexdigest() != asset.content_hash:
            raise ServiceError(
                "evidence_hash_mismatch",
                "Evidence content does not match its recorded hash",
                502,
                {"asset_id": asset.asset_id},
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return self._file(asset, "undecodable", None)
        return self._file(asset, "included", text)


@dataclass(frozen=True)
class EvidenceBundle:
    """Everything the judges see, split into the five untrusted sections."""

    specification: str
    inputs: tuple[EvidenceFile, ...]
    outputs: tuple[EvidenceFile, ...]
    claim: str
    rebuttal: str | None

    def sections(self) -> list[tuple[str, object]]:
        """Ordered (name, JSON-ready data) pairs for the judge prompt."""
        return [
            ("SPECIFICATION", self.specification),
            ("INPUT_MANIFEST", [f.manifest_entry("input") for f in self.inputs]),
            ("OUTPUT_MANIFEST", [f.manifest_entry("output") for f in self.outputs]),
            ("CLAIM", self.claim),
            ("REBUTTAL", self.rebuttal),
        ]


def build_evidence_bundle(task_data: dict[str, Any], dispute: dict[str, Any]) -> EvidenceBundle:
    """Assemble the bundle; missing spec or evidence files is an explicit error.

    Files uploaded by the poster (claimant) are inputs; files uploaded by the
    worker (respondent) are outputs. A file from anyone else is rejected.
    """
    spec = task_data.get("spec")
    if not isinstance(spec, str) or spec.strip() == "":
        raise _unavailable("Task specification is unavailable")
    files = task_data.get("deliverables")
    if not isinstance(files, list) or not all(isinstance(f, EvidenceFile) for f in files):
        raise _unavailable("Task evidence files are unavailable")
    poster_id = str(dispute["claimant_id"])
    worker_id = str(dispute["respondent_id"])
    inputs: list[EvidenceFile] = []
    outputs: list[EvidenceFile] = []
    for file in files:
        if file.uploader_id == poster_id:
            inputs.append(file)
        elif file.uploader_id == worker_id:
            outputs.append(file)
        else:
            raise ServiceError(
                "evidence_invalid",
                "Evidence file was not uploaded by a task party",
                502,
                {"asset_id": file.asset_id},
            )
    rebuttal = dispute["rebuttal"]
    return EvidenceBundle(
        specification=spec,
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        claim=str(dispute["claim"]),
        rebuttal=str(rebuttal) if rebuttal is not None else None,
    )
