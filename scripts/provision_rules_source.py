"""Fetch the Comprehensive Rules once, as a reproducible source artefact.

    python scripts/provision_rules_source.py
    python scripts/provision_rules_source.py --expect-sha256 <hex>

Provisioning, like downloading the retrieval models — not something any query
path does. It runs deliberately, writes a dated directory under
``data/reference/comprehensive_rules/``, and records enough to answer "which
rules text was this index built from" years later: the URL requested, the
effective date the document states about ITSELF, when it was retrieved, its
byte count and its content hash.

It fetches from ONE pinned URL. The legacy path tried four in turn and kept
whichever answered, which means an index built from it cannot say which
document it contains — a fallback that silently changes your source is worse
than a failure, because a failure is visible.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
SOURCE_SCHEMA = "research-rules-source.v1"
DEFAULT_URL = "https://media.wizards.com/2026/downloads/MagicCompRules%2020260819.txt"
#: The publisher's rules page, used only to REPORT whether a newer file is
#: listed. It is never followed automatically: discovering a new version and
#: silently indexing it would make the source depend on the day you ran this.
RULES_PAGE_URL = "https://magic.wizards.com/en/rules"
DEFAULT_ROOT = ROOT / "data" / "reference" / "comprehensive_rules"
#: The rules text is ~1 MB. Anything markedly smaller is an error page that
#: returned 200, which is the failure mode a status check alone misses.
MINIMUM_BYTES = 500_000
#: The document states its own effective date near the top, as
#: "These rules are effective as of February 27, 2026."
_EFFECTIVE = re.compile(
    r"effective as of\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})", re.IGNORECASE
)
_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def _effective_date(text: str) -> date:
    """Read the effective date the document states about itself.

    Args:
        text: The full rules text.

    Returns:
        The stated effective date.

    Raises:
        ValueError: If the document does not state one. A rules corpus whose
            version is inferred from a URL rather than read from the text is a
            guess, and this refuses to guess.
    """
    match = _EFFECTIVE.search(text[:20_000])
    if match is None:
        raise ValueError(
            "the document states no effective date; refusing to infer a "
            "version from the URL"
        )
    month_name, day, year = re.split(r"[\s,]+", match.group(1).strip())
    return date(int(year), _MONTHS.index(month_name.casefold()) + 1, int(day))


def _published_url() -> str | None:
    """Return the rules text URL the publisher currently lists, if readable."""
    try:
        page = httpx.get(RULES_PAGE_URL, timeout=45, follow_redirects=True)
        page.raise_for_status()
    except httpx.HTTPError:
        return None
    match = re.search(
        r"https://media\.wizards\.com/[^\"'<>]*MagicCompRules[^\"'<>]*\.txt",
        page.text,
    )
    return match.group(0).replace(" ", "%20") if match else None


def _write_atomic(path: Path, content: bytes) -> None:
    """Write EXACT BYTES.

    Text mode would be wrong here in two ways that cancel out of view. The
    rules file is CRLF with a UTF-8 BOM: writing a decoded string keeps the
    CRLFs, and reading it back with universal newlines collapses them, so the
    file hashes differently from the string that was hashed when it arrived.
    The archival artefact is the bytes the publisher served; normalisation is
    the build's job and is recorded separately there.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    """Fetch the rules once and write the source artefact beside them."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--expect-sha256",
        default="",
        help="refuse unless the fetched text hashes to this; use to re-verify "
        "an already-provisioned source on another machine",
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        help="ingest an already-downloaded rules text instead of fetching, for "
        "a machine with no outbound network",
    )
    parser.add_argument(
        "--check-current",
        action="store_true",
        help="report whether the publisher lists a newer rules file than --url",
    )
    args = parser.parse_args()

    if args.check_current:
        published = _published_url()
        if published is None:
            print("could not read the publisher's rules page")
        elif published == args.url:
            print(f"--url is the file the publisher currently lists: {published}")
        else:
            print(
                "the publisher lists a DIFFERENT file:\n"
                f"  --url:     {args.url}\n"
                f"  published: {published}\n"
                "re-run with --url set to it if that is the version you want"
            )

    if args.from_file is not None:
        raw = args.from_file.read_bytes()
        retrieved_from: str | None = None
        status: int | None = None
    else:
        response = httpx.get(args.url, timeout=120, follow_redirects=True)
        response.raise_for_status()
        raw = response.content
        retrieved_from = str(response.url)
        status = response.status_code

    if len(raw) < MINIMUM_BYTES:
        raise SystemExit(
            f"RULES SOURCE REFUSED: {len(raw)} bytes is too short to be the "
            "Comprehensive Rules; a 200 response is not proof of content"
        )
    text = raw.decode("utf-8-sig")
    digest = hashlib.sha256(raw).hexdigest()
    if args.expect_sha256 and digest != args.expect_sha256:
        raise SystemExit(
            "RULES SOURCE REFUSED: content hash mismatch\n"
            f"  expected: {args.expect_sha256}\n  fetched:  {digest}"
        )
    effective = _effective_date(text)

    directory = args.root / effective.isoformat()
    rules_path = directory / "comprehensive_rules.txt"
    source_path = directory / "source.json"
    if rules_path.is_file():
        existing = hashlib.sha256(rules_path.read_bytes()).hexdigest()
        if existing == digest:
            print(f"already provisioned, unchanged: {rules_path}")
            return 0
        raise SystemExit(
            f"RULES SOURCE REFUSED: {rules_path} already exists with different "
            "content for the same effective date. Two different documents "
            "claiming one version is exactly the ambiguity this layout prevents"
        )

    payload: dict[str, Any] = {
        "schema_version": SOURCE_SCHEMA,
        "document": "comprehensive_rules",
        "requested_url": args.url,
        "retrieved_from": retrieved_from,
        "http_status": status,
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "effective_date": effective.isoformat(),
        "content_sha256": digest,
        "byte_count": len(raw),
        "line_count": text.count("\n") + 1,
        "encoding": "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8",
        "line_ending": "crlf" if b"\r\n" in raw else "lf",
        "note": (
            "content_sha256 is over the EXACT BYTES served, so it can be "
            "checked with: shasum -a 256 comprehensive_rules.txt. the effective "
            "date is read from the document's own text, not from the URL"
        ),
    }
    _write_atomic(rules_path, raw)
    _write_atomic(
        source_path,
        (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(f"provisioned Comprehensive Rules effective {effective.isoformat()}")
    print(f"  {rules_path}")
    print(f"  sha256 {digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"RULES SOURCE REFUSED: {exc}")
        raise SystemExit(2) from None
