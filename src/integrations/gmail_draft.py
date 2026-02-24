"""Minimal Gmail draft stub.

This V1 writes a draft_request.json payload that is ready for Gmail API usage.
It avoids OAuth flows in this environment while preserving a deterministic output.
"""
import json
from pathlib import Path
from typing import Optional


def create_draft(subject: str, body: str, to: str, output_dir: Optional[Path] = None) -> dict:
    """Create a draft payload for Gmail API.

    Returns the payload dict and writes it to draft_request.json in output_dir if provided.
    """
    payload = {
        "message": {
            "headers": {
                "To": to,
                "Subject": subject,
            },
            "body": body,
        }
    }

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "draft_request.json").write_text(json.dumps(payload, indent=2))

    return payload
