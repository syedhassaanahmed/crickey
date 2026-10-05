from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESCRIPTION = (
    "crickey is an MCP server that answers cricket statistics questions from Cricinfo Statsguru."
)
OLD_DISCLAIMER = "crickey is for personal use at the user's own risk"


def test_dockerfile_and_release_share_the_image_description() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    assert dockerfile.count(DESCRIPTION) == 1
    assert release.count(DESCRIPTION) == 2
    assert OLD_DISCLAIMER not in dockerfile
    assert OLD_DISCLAIMER not in release
