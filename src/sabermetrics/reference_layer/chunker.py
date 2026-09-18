"""Document chunker for reference material (D3.4).

Splits documents into ~500-token chunks with semantic boundary respect:
- Comprehensive Rules: chunk by section number (CR 100, CR 101, etc.)
- Articles: chunk by paragraph clusters with overlap
- Each chunk gets metadata: document, section, tier
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A chunk of reference text with metadata."""

    id: str
    document: str
    section: str | None
    tier: int
    content: str


def chunk_id(document: str, section: str | None, content: str) -> str:
    """Return a stable identity for one chunk of reference text.

    Chunk ids used to be ``uuid4()``. That made
    :func:`~sabermetrics.reference_layer.indexer.reference_content_sha256`
    unreproducible, because it hashes chunk identity — so the same source text
    rebuilt twice produced two different corpus hashes and therefore two
    different generation ids, and an upsert keyed on a random id could only
    ever insert. A content-addressed artefact that is not addressed by its
    content is the defect this project exists to avoid.

    Derived from the document, the section label and the chunk text, so
    identical source text yields an identical id and a rebuild is idempotent.

    Args:
        document: Source document label, e.g. ``"comprehensive_rules"``.
        section: Section label, when the chunker identified one.
        content: The chunk text.

    Returns:
        A 32-character hex digest.
    """
    payload = "\u241f".join((document, section or "", content.strip()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


#: A numbered rule, e.g. "100.1." or "702.21a". The table of contents lists
#: section titles only, so it contains none of these.
_RULE_LINE = re.compile(r"^\d{3}\.\d")
#: The start of one numbered rule, in either printed form: ``100.1.`` (period
#: after the number, used by unlettered rules) or ``702.21a`` (letter suffix,
#: no period). The number is captured so a chunk can be labelled with the rule
#: its text begins with.
_RULE_START = re.compile(r"^(\d{3}\.\d+[a-z]?)\.?\s", re.MULTILINE)
#: The body's Glossary and Credits headings, each on a line of its own.
_GLOSSARY_HEADING = re.compile(r"^Glossary[ \t]*$", re.MULTILINE)
_CREDITS_HEADING = re.compile(r"^Credits[ \t]*$", re.MULTILINE)
#: A section heading, e.g. "100. General".
_SECTION_LINE = re.compile(r"^\d{3}\.\s")


def strip_table_of_contents(text: str) -> str:
    """Drop the Comprehensive Rules' contents listing.

    The contents lists every section by title, in the same ``NNN. Title`` form
    the body uses for its headings. The section splitter cannot tell the two
    apart, so it produced a chunk per listed title whose entire content was the
    title — "Commander", "General" — and those chunks then competed with the
    real rules text for the same queries and won, because a one-word chunk is a
    dense match for a one-word topic.

    They were also the source of the only content-addressed id collision in the
    corpus: "CR 600" titled "General" appears once in the contents and once in
    the body.

    The boundary is structural rather than a length threshold. The contents
    contains no numbered rule line and the body begins with one, so the body
    starts at the section heading that precedes the first numbered rule.

    Args:
        text: The full rules text.

    Returns:
        The text from the body's first section heading onward, or the text
        unchanged if the expected structure is absent.
    """
    lines = text.split("\n")
    first_rule = next(
        (index for index, line in enumerate(lines) if _RULE_LINE.match(line)), None
    )
    if first_rule is None:
        return text
    heading = next(
        (
            index
            for index in range(first_rule, -1, -1)
            if _SECTION_LINE.match(lines[index])
        ),
        None,
    )
    if heading is None:
        return text
    return "\n".join(lines[heading:])


class DocumentChunker:
    """Splits reference documents into semantically coherent chunks."""

    # Approximate tokens per chunk target
    TARGET_CHUNK_TOKENS: int = 500
    # Approximate chars per token (conservative estimate)
    CHARS_PER_TOKEN: float = 4.0
    #: A HARD ceiling, in characters, which no chunk may exceed.
    #:
    #: Not a preference. The embedding model reads 512 tokens and stops, so
    #: every character past that point in a chunk contributes NOTHING to the
    #: vector that retrieval scores — the text is indexed, searchable by the
    #: lexical stage, and invisible to the dense one. Before this bound, 144 of
    #: the 400 Comprehensive Rules chunks were over the window and one was
    #: 28,379 tokens: the Glossary, which has no rule numbers for the
    #: sub-section splitter to cut on and so was swallowed whole into the
    #: chunk of the last numbered rule before it.
    #:
    #: 1,250 characters. The ratio is not uniform: prose runs ~4.1 chars per
    #: token in this document, the subtype lists in rule 205 ~3.0, and 107.3n
    #: — a rule about the variable X, which the tokenizer splits on every
    #: symbol — 2.55. At that ratio 512 tokens is 1,305 characters, so 1,250
    #: is the largest round ceiling that clears every chunk measured, with
    #: margin. The condition that actually matters is asserted in TOKENS by
    #: the tests, which may load a tokenizer; this module may not, so the bound
    #: it enforces is a proxy and the test is the check.
    MAX_CHUNK_CHARS: int = 1250

    def chunk_comprehensive_rules(self, rules_path: Path) -> list[Chunk]:
        """Chunk Comprehensive Rules by section number.

        Sections are identified by patterns like "100.", "100.1", "702.21a".
        Tier 1 = highest priority reference material.

        Args:
            rules_path: Path to comprehensive_rules.txt.

        Returns:
            List of Chunks with section metadata.
        """
        if not rules_path.exists():
            logger.warning("Rules file not found: %s", rules_path)
            return []

        text = strip_table_of_contents(
            rules_path.read_text(encoding="utf-8", errors="replace")
        )
        chunks: list[Chunk] = []

        # The Glossary and the credits follow the last numbered rule and carry
        # no rule numbers, so they are chunked apart from the body under labels
        # that name what they are. The contents listing has already been
        # stripped, so the heading found here is the body's.
        back_matter = ""
        glossary_heading = None
        for glossary_heading in _GLOSSARY_HEADING.finditer(text):
            pass
        if glossary_heading is not None:
            back_matter = text[glossary_heading.start() :]
            text = text[: glossary_heading.start()]

        # Split by top-level section numbers (e.g., "100. General", "702. Keyword Abilities")
        # Pattern: line starting with a number followed by a period
        section_pattern = re.compile(r"^(\d{3})\.\s", re.MULTILINE)
        sections = section_pattern.split(text)

        # sections alternates: [preamble, "100", content, "101", content, ...]
        current_section = "preamble"
        for i, part in enumerate(sections):
            if re.fullmatch(r"\d{3}", part):
                current_section = part
                continue

            if not part.strip():
                continue

            section_label = (
                f"CR {current_section}" if current_section != "preamble" else "preamble"
            )

            # Further split large sections into sub-chunks
            sub_chunks = self._split_by_size(part, section_label)
            for sub_content, sub_section in sub_chunks:
                chunks.append(
                    Chunk(
                        id=chunk_id("comprehensive_rules", sub_section, sub_content),
                        document="comprehensive_rules",
                        section=sub_section,
                        tier=1,
                        content=sub_content.strip(),
                    )
                )

        if back_matter:
            chunks.extend(self._chunk_back_matter(back_matter))

        logger.info("Chunked Comprehensive Rules into %d chunks", len(chunks))
        return chunks

    def chunk_commander_rules(self, rules_path: Path) -> list[Chunk]:
        """Chunk Commander-specific rules.

        Tier 1 = highest priority (Commander is our target format).

        Args:
            rules_path: Path to commander_rules.txt.

        Returns:
            List of Chunks.
        """
        if not rules_path.exists():
            logger.warning("Commander rules file not found: %s", rules_path)
            return []

        text = rules_path.read_text(encoding="utf-8", errors="replace")
        chunks: list[Chunk] = []

        # Split by paragraphs
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        # Group paragraphs into chunks of appropriate size
        current_content: list[str] = []
        current_length = 0
        target_chars = int(self.TARGET_CHUNK_TOKENS * self.CHARS_PER_TOKEN)

        for para in paragraphs:
            current_content.append(para)
            current_length += len(para)

            if current_length >= target_chars:
                chunks.append(
                    Chunk(
                        id=chunk_id(
                            "commander_rules", None, "\n\n".join(current_content)
                        ),
                        document="commander_rules",
                        section=None,
                        tier=1,
                        content="\n\n".join(current_content),
                    )
                )
                current_content = []
                current_length = 0

        # Remaining content
        if current_content:
            chunks.append(
                Chunk(
                    id=chunk_id("commander_rules", None, "\n\n".join(current_content)),
                    document="commander_rules",
                    section=None,
                    tier=1,
                    content="\n\n".join(current_content),
                )
            )

        logger.info("Chunked Commander rules into %d chunks", len(chunks))
        return chunks

    def chunk_article(self, article_path: Path, tier: int = 3) -> list[Chunk]:
        """Chunk a strategic article by paragraph clusters with overlap.

        Args:
            article_path: Path to the article text file.
            tier: Reference tier (default 3 for articles).

        Returns:
            List of Chunks.
        """
        if not article_path.exists():
            logger.warning("Article file not found: %s", article_path)
            return []

        text = article_path.read_text(encoding="utf-8", errors="replace")
        # Derive document name from filename
        doc_name = article_path.stem

        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: list[Chunk] = []
        target_chars = int(self.TARGET_CHUNK_TOKENS * self.CHARS_PER_TOKEN)

        # Sliding window with 1-paragraph overlap
        current_content: list[str] = []
        current_length = 0

        for para in paragraphs:
            current_content.append(para)
            current_length += len(para)

            if current_length >= target_chars:
                chunks.append(
                    Chunk(
                        id=chunk_id(doc_name, None, "\n\n".join(current_content)),
                        document=doc_name,
                        section=None,
                        tier=tier,
                        content="\n\n".join(current_content),
                    )
                )
                # Keep last paragraph as overlap for context continuity
                if current_content:
                    overlap = current_content[-1]
                    current_content = [overlap]
                    current_length = len(overlap)
                else:
                    current_content = []
                    current_length = 0

        if current_content:
            chunks.append(
                Chunk(
                    id=chunk_id(doc_name, None, "\n\n".join(current_content)),
                    document=doc_name,
                    section=None,
                    tier=tier,
                    content="\n\n".join(current_content),
                )
            )

        logger.info("Chunked article '%s' into %d chunks", doc_name, len(chunks))
        return chunks

    def chunk_mechanics_article(self, article_path: Path) -> list[Chunk]:
        """Chunk a WotC set mechanics article by mechanic sections.

        Mechanics articles describe keyword abilities and set mechanics.
        Each mechanic heading gets its own chunk when possible, preserving
        the full explanation for that mechanic. Falls back to paragraph
        clustering for articles without clear mechanic headings.

        Tier 2 = strategic reference (these define how mechanics work).

        Args:
            article_path: Path to the mechanics article text file.

        Returns:
            List of Chunks with mechanic-aware boundaries.
        """
        if not article_path.exists():
            logger.warning("Mechanics article not found: %s", article_path)
            return []

        text = article_path.read_text(encoding="utf-8", errors="replace")
        doc_name = article_path.stem  # e.g. "mechanics_kaldheim"

        # Extract set name from header if present
        set_name = doc_name
        if text.startswith("Set Mechanics Article:"):
            first_line = text.split("\n", 1)[0]
            set_name = first_line.replace("Set Mechanics Article:", "").strip()

        # Try to split by mechanic headings (all-caps lines or lines that
        # look like section headers: short lines followed by longer content)
        sections = self._split_mechanics_by_heading(text)

        if len(sections) > 1:
            return self._chunk_mechanic_sections(sections, doc_name, set_name)

        # Fallback: use standard article chunking at tier 2
        return self.chunk_article(article_path, tier=2)

    def _split_mechanics_by_heading(self, text: str) -> list[tuple[str, str]]:
        """Split mechanics article text into (heading, body) pairs.

        Detects headings by looking for short lines (< 60 chars) that
        are followed by longer explanatory text, or lines in ALL CAPS.

        Returns:
            List of (heading, body) tuples. If no headings found,
            returns a single ("", full_text) entry.
        """
        lines = text.split("\n")
        sections: list[tuple[str, str]] = []
        current_heading = ""
        current_body: list[str] = []

        for line in lines:
            stripped = line.strip()
            # Skip header metadata
            if stripped.startswith("Set Mechanics Article:"):
                continue
            if stripped.startswith("Source:"):
                continue
            if stripped == "---":
                continue

            # Detect heading: short line, not empty, followed by content
            is_heading = (
                len(stripped) > 2
                and len(stripped) < 60
                and not stripped.endswith(".")
                and not stripped.endswith(",")
                and (
                    stripped.isupper()
                    or (stripped[0].isupper() and stripped.count(" ") < 8)
                )
                and not any(
                    stripped.lower().startswith(w)
                    for w in ("the ", "a ", "an ", "if ", "when ", "for ")
                )
            )

            if is_heading and current_body:
                body_text = "\n".join(current_body).strip()
                if body_text:
                    sections.append((current_heading, body_text))
                current_heading = stripped
                current_body = []
            elif is_heading and not current_body:
                current_heading = stripped
            else:
                current_body.append(line)

        # Last section
        if current_body:
            body_text = "\n".join(current_body).strip()
            if body_text:
                sections.append((current_heading, body_text))

        return sections

    def _chunk_mechanic_sections(
        self,
        sections: list[tuple[str, str]],
        doc_name: str,
        set_name: str,
    ) -> list[Chunk]:
        """Create chunks from mechanic sections, merging small ones.

        Args:
            sections: List of (heading, body) pairs.
            doc_name: Document identifier for the chunk.
            set_name: Human-readable set name for context.

        Returns:
            List of Chunks at tier 2.
        """
        target_chars = int(self.TARGET_CHUNK_TOKENS * self.CHARS_PER_TOKEN)
        chunks: list[Chunk] = []

        pending_content: list[str] = []
        pending_section: str | None = None
        pending_length = 0

        for heading, body in sections:
            section_text = f"{heading}\n\n{body}" if heading else body
            context_text = f"[{set_name}] {section_text}"

            if pending_length + len(context_text) > target_chars and pending_content:
                # Flush pending
                chunks.append(
                    Chunk(
                        id=chunk_id(
                            doc_name, pending_section, "\n\n".join(pending_content)
                        ),
                        document=doc_name,
                        section=pending_section,
                        tier=2,
                        content="\n\n".join(pending_content),
                    )
                )
                pending_content = []
                pending_length = 0
                pending_section = None

            pending_content.append(context_text)
            pending_length += len(context_text)
            if pending_section is None and heading:
                pending_section = heading

        # Flush remaining
        if pending_content:
            chunks.append(
                Chunk(
                    id=chunk_id(
                        doc_name, pending_section, "\n\n".join(pending_content)
                    ),
                    document=doc_name,
                    section=pending_section,
                    tier=2,
                    content="\n\n".join(pending_content),
                )
            )

        logger.info(
            "Chunked mechanics article '%s' into %d chunks",
            doc_name,
            len(chunks),
        )
        return chunks

    def chunk_game_changers(self, yaml_path: Path) -> list[Chunk]:
        """Create reference chunk from game changers list.

        Args:
            yaml_path: Path to game_changers.yaml.

        Returns:
            Single Chunk containing the game changer list.
        """
        import yaml

        if not yaml_path.exists():
            return []

        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}

        changers = data.get("game_changers", [])
        if not changers:
            return []

        lines = ["WotC Official Game Changer Cards (Bracket Framework):", ""]
        for gc in changers:
            name = gc.get("card_name", "")
            bracket = gc.get("bracket_threshold", "?")
            rationale = gc.get("rationale", "")
            lines.append(f"- {name} (bracket {bracket}): {rationale}")

        return [
            Chunk(
                id=chunk_id("game_changers", None, "\n".join(lines)),
                document="game_changers",
                section=None,
                tier=2,
                content="\n".join(lines),
            )
        ]

    def _split_by_size(self, text: str, section_label: str) -> list[tuple[str, str]]:
        """Split one section's text into bounded chunks, each cited correctly.

        A chunk is labelled with the rule its text BEGINS with. The previous
        version set the label on every rule it passed and flushed only when a
        size target was reached, so a chunk accumulating 707.3 through 707.8
        was cited ``CR 707.8`` — and text with no rule numbers at all inherited
        whatever label happened to be current. On the pinned document that put
        an unambiguously wrong citation on 214 of 871 chunks, 83 of them the
        Glossary cited as a conspiracy-draft rule. The rules-support matcher
        keys on quote text and could not see it; a reader of the citation
        could.

        The rule number stays in the body: a person reading a cited passage
        should see the number, and the quote matcher already accepts a quote
        with or without it. Both the period form (``707.3.``) and the letter
        form (``707.2c``) start a rule; the earlier splitter accepted only the
        letter form, which is why one 5,000-character run was uninterruptible.

        Consecutive rules are packed together up to the hard ceiling, never
        past it, so the ceiling is met at rule boundaries wherever the text
        allows and the blind whitespace cut in :meth:`_enforce_ceiling` is
        reached only by a single rule longer than the window. A continuation
        piece keeps its rule's label, because that is the rule it continues.

        Returns:
            List of (content, section) tuples.
        """
        bound = min(
            int(self.TARGET_CHUNK_TOKENS * self.CHARS_PER_TOKEN), self.MAX_CHUNK_CHARS
        )
        starts = [match.start() for match in _RULE_START.finditer(text)]
        segments: list[tuple[str, str]] = []
        if not starts:
            segments.append((text, section_label))
        else:
            if text[: starts[0]].strip():
                segments.append((text[: starts[0]], section_label))
            for index, start in enumerate(starts):
                end = starts[index + 1] if index + 1 < len(starts) else len(text)
                segment = text[start:end]
                number = _RULE_START.match(segment)
                assert number is not None  # finditer found it at this offset
                segments.append((segment, f"CR {number.group(1)}"))

        packed: list[tuple[str, str]] = []
        current_text = ""
        current_label = section_label
        for segment, label in segments:
            if current_text and len(current_text) + len(segment) > bound:
                packed.append((current_text, current_label))
                current_text = ""
            if not current_text:
                current_label = label
            current_text += segment
        if current_text.strip():
            packed.append((current_text, current_label))

        return [
            (piece, label)
            for content, label in packed
            for piece in self._enforce_ceiling(content)
        ]

    def _chunk_back_matter(self, back_matter: str) -> list[Chunk]:
        """Chunk the Glossary and the credits under labels that say what they are.

        Neither carries a rule number, so the section splitter has nothing to
        cut on and the old code swallowed both into the last numbered rule's
        chunk under that rule's label. A glossary chunk is cited by the first
        term it defines; the credits are cited as ``Credits`` at a low tier so
        a trademark notice never outranks a rule.

        Args:
            back_matter: Text from the body's ``Glossary`` heading to the end.

        Returns:
            Chunks in document order.
        """
        credits_match = _CREDITS_HEADING.search(back_matter)
        glossary = (
            back_matter[: credits_match.start()] if credits_match else back_matter
        )
        credits = back_matter[credits_match.start() :] if credits_match else ""
        glossary = _GLOSSARY_HEADING.sub("", glossary, count=1)

        chunks: list[Chunk] = []
        entries = [entry for entry in glossary.split("\n\n") if entry.strip()]
        current: list[str] = []
        current_size = 0

        def flush() -> None:
            if not current:
                return
            body = "\n\n".join(current)
            term = current[0].strip().splitlines()[0].strip()
            label = f"Glossary: {term}"
            for piece in self._enforce_ceiling(body):
                chunks.append(
                    Chunk(
                        id=chunk_id("comprehensive_rules", label, piece),
                        document="comprehensive_rules",
                        section=label,
                        tier=1,
                        content=piece.strip(),
                    )
                )
            current.clear()

        for entry in entries:
            if current and current_size + len(entry) + 2 > self.MAX_CHUNK_CHARS:
                flush()
                current_size = 0
            current.append(entry)
            current_size += len(entry) + 2
        flush()

        for piece in self._enforce_ceiling(credits):
            if piece.strip():
                chunks.append(
                    Chunk(
                        id=chunk_id("comprehensive_rules", "Credits", piece),
                        document="comprehensive_rules",
                        section="Credits",
                        tier=3,
                        content=piece.strip(),
                    )
                )
        return chunks

    def _enforce_ceiling(self, text: str) -> list[str]:
        """Split text on paragraph boundaries until every piece fits the window.

        Args:
            text: One chunk's content.

        Returns:
            Pieces, each within ``MAX_CHUNK_CHARS``. A single paragraph longer
            than the ceiling is cut on whitespace rather than left oversized,
            because half a paragraph that the encoder reads beats a whole one
            it does not.
        """
        if len(text) <= self.MAX_CHUNK_CHARS:
            return [text]
        pieces: list[str] = []
        current = ""
        for paragraph in text.split("\n\n"):
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= self.MAX_CHUNK_CHARS:
                current = candidate
                continue
            if current:
                pieces.append(current)
                current = ""
            while len(paragraph) > self.MAX_CHUNK_CHARS:
                # A sentence boundary first, whitespace only as a last resort.
                # The rules-support key quotes whole sentences and requires a
                # quote to sit inside one chunk, so a cut inside a sentence
                # makes that sentence unmatchable forever; a cut between
                # sentences never does. Only accepted when it keeps at least
                # half the window, so a long sentence does not degrade into a
                # run of tiny pieces.
                cut = paragraph.rfind(". ", 0, self.MAX_CHUNK_CHARS)
                if cut > self.MAX_CHUNK_CHARS // 2:
                    cut += 1
                else:
                    cut = paragraph.rfind(" ", 0, self.MAX_CHUNK_CHARS)
                    if cut <= 0:
                        cut = self.MAX_CHUNK_CHARS
                pieces.append(paragraph[:cut])
                paragraph = paragraph[cut:].lstrip()
            current = paragraph
        if current.strip():
            pieces.append(current)
        return [piece for piece in pieces if piece.strip()]
