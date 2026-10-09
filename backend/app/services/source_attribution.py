"""Bounded public attribution frozen in the import mapping, with no disk reads."""

from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from app.services.public_lexicon_preview import RevisionDeclaration

TEXT_KEYS = {"creators", "modifications", "copyright_notice", "disclaimer", "snapshot_label"}
URL_KEYS = {"license_url", "source_url", "snapshot_url"}
KEYS = TEXT_KEYS | URL_KEYS | {"snapshot_sha256", "history_url_template", "links"}


def public_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        return (value.startswith("https://") and bool(parsed.hostname)
                and not parsed.username and not parsed.password and "\\" not in value
                and not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value))
    except ValueError:
        return False


def validate_attribution(value: object) -> dict:
    if not isinstance(value, dict) or set(value) - KEYS:
        raise ValueError("attribution must contain only public statement fields")
    for key, text in value.items():
        if key == "links":
            continue
        if not isinstance(text, str) or len(text) > 3000:
            raise ValueError(f"attribution {key} must be bounded text")
        if key in URL_KEYS and not public_url(text):
            raise ValueError(f"attribution {key} must be a public HTTPS URL")
    for key in ("creators", "modifications", "license_url"):
        if not value.get(key, "").strip():
            raise ValueError(f"attribution requires {key}")
    if "snapshot_sha256" in value and not re.fullmatch(r"[a-f0-9]{64}", value["snapshot_sha256"]):
        raise ValueError("attribution snapshot_sha256 must be a SHA-256")
    if "history_url_template" in value:
        RevisionDeclaration(value="history", url_template=value["history_url_template"])
    links = value.get("links", [])
    if not isinstance(links, list) or len(links) > 12:
        raise ValueError("attribution links must be a bounded list")
    for link in links:
        if (not isinstance(link, dict) or set(link) != {"label", "url"}
                or not isinstance(link["label"], str) or not 0 < len(link["label"]) <= 120
                or not isinstance(link["url"], str) or not public_url(link["url"])):
            raise ValueError("attribution link requires a label and public HTTPS URL")
    return json.loads(json.dumps(value))


def frozen_attribution(artifact) -> dict:
    try:
        mapping = json.loads(artifact.mapping_json or "")
        value = mapping.get("attribution") if isinstance(mapping, dict) else None
        return validate_attribution(value) if value is not None else {}
    except (TypeError, ValueError):
        return {}


def has_fixed_package(value: dict) -> bool:
    return bool(value.get("snapshot_url") and value.get("snapshot_sha256"))
