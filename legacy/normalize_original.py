# -*- coding: utf-8 -*-  # Ensure UTF-8 source encoding for consistent handling.
"""Utilities for normalising original transcripts before alignment."""
from __future__ import annotations  # Postpone evaluation of annotations for Python 3.13 compatibility.

import argparse  # Parse command-line interface arguments.
import json  # Load JSON structures such as compat maps and words data.
import logging  # Provide structured runtime logging for diagnostics.
import unicodedata  # Execute Unicode normalisation and character property checks.
import warnings  # Emit deprecation notices for legacy helpers.
from collections import Counter  # Count character frequencies and gather grouped statistics.
from dataclasses import dataclass, field  # Define lightweight containers for accumulating metrics.
from pathlib import Path  # Work with filesystem paths in a platform-independent way.
from typing import Dict, List, Mapping, Optional, Sequence, Tuple  # Provide type hints for clarity.

try:  # Wrap optional dependency discovery in a safe guard.
    from opencc import OpenCC  # type: ignore  # Attempt to use OpenCC if available for optional conversions.
    _HAS_OPENCC = True  # Flag to indicate OpenCC can be used when requested.
except Exception:  # Catch import errors or runtime failures gracefully.
    OpenCC = None  # type: ignore  # Fallback to None to simplify later checks.
    _HAS_OPENCC = False  # Mark that OpenCC-related functionality should be skipped.

# Create a module-level logger for consistent diagnostic output.
LOGGER = logging.getLogger(__name__)  # Logger named after the module for contextual messages.

warnings.warn(
    "onepass.normalize_original 已弃用：默认参数与 all-in-one 不再同步，仅面向旧流水线。",
    DeprecationWarning,
    stacklevel=2,
)

# Define punctuation endpoints that strongly indicate sentence boundaries.
SENTENCE_ENDINGS = "。！？：；…"  # Characters that should prevent line merging when found at the end of a line.

# Enumerate Unicode characters considered invisible and thus removable.
INVISIBLE_CHARS = {  # Set of invisible or whitespace-like characters targeted for removal.
    "\u200b",  # Zero width space.
    "\u200c",  # Zero width non-joiner.
    "\u200d",  # Zero width joiner.
    "\ufeff",  # Byte-order mark often appearing mid-text.
    "\u2060",  # Word joiner used in some OCR outputs.
    "\u00a0",  # Non-breaking space.
}  # Using a set allows O(1) membership checks.

# Specify ASCII punctuation that should be mapped to Chinese equivalents under zh style.
PUNCT_MAP_ZH = {  # Mapping table for converting ASCII punctuation to Chinese equivalents.
    ",": "，",  # Standard comma.
    ".": "。",  # Full stop.
    "?": "？",  # Question mark.
    "!": "！",  # Exclamation mark.
    ":": "：",  # Colon.
    ";": "；",  # Semicolon.
    "(": "（",  # Opening parenthesis.
    ")": "）",  # Closing parenthesis.
    "[": "「",  # Map square brackets to corner quotes for readability.
    "]": "」",  # Matching closing corner quote.
    "{": "『",  # Map curly brackets to book title quotes.
    "}": "』",  # Closing book title quote.
    "\"": "”",  # Convert double quotes to Chinese closing quotes; pairing handled elsewhere.
    "'": "’",  # Convert apostrophes to Chinese single quote.
    "<": "《",  # Opening book title mark.
    ">": "》",  # Closing book title mark.
    "-": "——",  # Map hyphen-minus to full dash for emphasis lines.
    "_": "——",  # Treat underscore similarly to represent emphasised dash.
}  # Table ensures punctuation becomes consistent with ASR expectations.

# Provide reverse mapping for English punctuation should en style be requested later.
PUNCT_MAP_EN = {value: key for key, value in PUNCT_MAP_ZH.items()}  # Generate inverse dictionary programmatically.

# Precompile repeated regular expressions once for efficiency.
ELLIPSIS_VARIANTS = ("...", "。。。,", "。。。。", "．．．")  # Known ellipsis strings to replace with a single ellipsis.
DASH_VARIANTS = ("—", "–", "－", "―", "―", "—", "——")  # Various dash characters observed in OCR/ASR workflows.

# Candidate keywords indicating non-body front or back matter for Chinese content.
FRONT_MATTER_KEYWORDS = [  # Keywords marking front or back matter sections.
    "目 录",  # OCR often inserts spaces within catalogue headings.
    "目录",  # Direct Chinese word for table of contents.
    "封底",  # Back cover notice.
    "版权",  # Copyright page section.
    "致谢",  # Acknowledgement front matter.
    "献词",  # Dedication text.
    "前言",  # Prologue often repeated before chapters.
    "编者按",  # Editor notes.
    "作者",  # Author line in headings.
    "译者",  # Translator credit.
    "ISBN",  # International book identifier.
    "出版",  # Publication data.
]  # List can be expanded in config if required.

# Define dataclass capturing statistics produced during normalisation steps.
@dataclass  # Dataclass decorator simplifies boilerplate for statistics container.
class NormalizationStats:  # Track counts and warnings produced during normalisation.
    """Accumulate counters for reporting."""
    compat_replacements: Counter = field(default_factory=Counter)  # Count of compatibility characters replaced.
    compat_missing: Counter = field(default_factory=Counter)  # Characters detected without mapping.
    punctuation_changes: Counter = field(default_factory=Counter)  # Statistics for punctuation mutations.
    whitespace_events: Counter = field(default_factory=Counter)  # Record whitespace cleanup operations.
    front_matter_removed: List[str] = field(default_factory=list)  # Removed front/back matter snippets.
    warnings: List[str] = field(default_factory=list)  # Collected warning messages for the final report.

# Represent comparison metrics between normalised text and ASR words.
@dataclass  # Dataclass wrapper for comparison results.
class ComparisonMetrics:  # Hold cross-check metrics for reporting.
    """Hold computed diff metrics for the markdown report."""
    text_only_chars: Counter  # Characters present only in the text after normalisation.
    words_only_chars: Counter  # Characters present only in the ASR words list.
    punctuation_overlap: float  # Ratio of punctuation overlap with the words data.
    ngram_mismatches: List[Tuple[str, int, int, str]]  # Each entry holds n-gram, length, line number, snippet.
    align_score: float  # Estimated friendliness score for downstream alignment.

# Provide helper dataclass for returning both text and metadata from pipeline stages.
@dataclass  # Dataclass linking transformed text with shared stats.
class StageResult:  # Propagate both text and statistics through pipeline.
    """Wrap a transformed string with updated statistics."""
    text: str  # The transformed text output from the stage.
    stats: NormalizationStats  # Reference to the shared statistics accumulator.

# Interpret boolean flags in a human-friendly way for CLI options.
def parse_bool(value: Optional[str]) -> bool:  # Normalise textual boolean flags from CLI inputs.
    """Convert typical string inputs into boolean values."""
    if value is None:  # If the flag is not provided default to False.
        return False  # Lack of flag equals False.
    if isinstance(value, bool):  # Accept direct boolean values for programmatic calls.
        return value  # Already the desired type.
    lowered = str(value).strip().lower()  # Normalise user input for comparison.
    return lowered in {"1", "true", "yes", "on"}  # Allow several truthy spellings.

# Read the source transcript and log encoding traces.
def load_text(path: Path, stats: NormalizationStats) -> StageResult:  # Load raw text while capturing encoding traces.
    """Load UTF-8 text from disk while recording BOM usage."""
    resolved = Path(path)  # Ensure the path is a Path object for consistency.
    data = resolved.read_bytes()  # Read raw bytes to inspect potential BOM sequences.
    if data.startswith(b"\xef\xbb\xbf"):  # Detect UTF-8 BOM prefix.
        stats.whitespace_events["bom_removed"] += 1  # Record BOM removal event for reporting.
        text = data.decode("utf-8-sig")  # Decode while stripping the BOM automatically.
    else:  # Otherwise treat as plain UTF-8.
        text = data.decode("utf-8")  # Decode assuming UTF-8 encoding per project standard.
    LOGGER.debug("Loaded %s characters from %s", len(text), resolved)  # Emit debug log for traceability.
    return StageResult(text=text, stats=stats)  # Return text along with stats reference.

# Apply Unicode NFKC normalisation.
def apply_nfkc(stage: StageResult) -> StageResult:  # Apply Unicode NFKC folding to the text payload.
    """Perform NFKC normalisation on the text."""
    normalised = unicodedata.normalize("NFKC", stage.text)  # Use built-in unicodedata for compatibility folding.
    stage.stats.whitespace_events["nfkc"] += 1  # Mark that NFKC was applied for audit purposes.
    return StageResult(text=normalised, stats=stage.stats)  # Return updated stage container.

# Load compatibility mapping table from JSON file.
def load_compat_map(path: Path) -> Dict[str, str]:  # Load JSON compatibility mapping file.
    """Load compatibility character mapping from JSON."""
    with Path(path).open("r", encoding="utf-8") as handle:  # Open mapping file as UTF-8 text.
        mapping = json.load(handle)  # Parse JSON content into dictionary.
    return mapping  # Provide the mapping for further use.

# Replace compatibility characters according to mapping and detect leftovers.
def map_compat_chars(stage: StageResult, mapping: Mapping[str, str]) -> StageResult:  # Replace mapped compatibility characters.
    """Replace known compatibility characters and capture unknown occurrences."""
    chars = []  # Accumulate processed characters for the resulting string.
    for ch in stage.text:  # Iterate through every character to inspect.
        if ch in mapping:  # When the character is present in the mapping table.
            replacement = mapping[ch]  # Lookup the mapped character.
            stage.stats.compat_replacements[ch] += 1  # Increment replacement counter.
            chars.append(replacement)  # Append the mapped character to the output buffer.
            continue  # Skip further checks for this character.
        name = unicodedata.name(ch, "")  # Retrieve Unicode name to inspect compatibility hints.
        if "COMPATIBILITY" in name or "KANGXI" in name:  # Detect leftover compatibility forms lacking mappings.
            stage.stats.compat_missing[ch] += 1  # Count the unmapped compatibility character.
        chars.append(ch)  # Append the original character when no mapping occurs.
    mapped_text = "".join(chars)  # Combine processed characters back into a string.
    return StageResult(text=mapped_text, stats=stage.stats)  # Return stage result for chaining.

# Optionally convert text to simplified Chinese when OpenCC is available.
def apply_opencc(stage: StageResult, enabled: bool) -> StageResult:  # Optionally convert text via OpenCC when available.
    """Convert traditional characters to simplified using OpenCC when requested."""
    if not enabled:  # Skip processing if the flag is not set.
        return stage  # Return stage unchanged.
    if not _HAS_OPENCC:  # Guard against missing OpenCC installation.
        stage.stats.warnings.append("OpenCC requested but not available; skipping conversion.")  # Log warning for report.
        return stage  # Leave text untouched.
    converter = OpenCC("t2s")  # type: ignore  # Instantiate OpenCC converter for traditional-to-simplified.
    converted = converter.convert(stage.text)  # type: ignore  # Perform the conversion.
    stage.stats.whitespace_events["opencc"] += 1  # Record that OpenCC was applied.
    return StageResult(text=converted, stats=stage.stats)  # Provide updated stage result.

# Normalise punctuation according to chosen style.
def normalize_punct(stage: StageResult, style: str = "zh") -> StageResult:  # Harmonise punctuation style.
    """Unify punctuation characters to the requested style."""
    text = stage.text  # Start from incoming text.
    if style == "zh":  # Apply Chinese punctuation mapping rules.
        replacements = []  # Collect characters for final string.
        for ch in text:  # Iterate over each character.
            if ch in PUNCT_MAP_ZH:  # Replace ASCII punctuation when necessary.
                stage.stats.punctuation_changes[ch] += 1  # Track each replacement event.
                replacements.append(PUNCT_MAP_ZH[ch])  # Append mapped punctuation.
            else:  # Retain characters not subject to mapping.
                replacements.append(ch)  # Append original character.
        text = "".join(replacements)  # Reassemble the string after mapping.
    elif style == "en":  # Provide reverse mapping if English punctuation is desired.
        replacements = []  # Buffer for mapped characters.
        for ch in text:  # Iterate through characters.
            if ch in PUNCT_MAP_EN:  # When Chinese punctuation has an ASCII equivalent.
                stage.stats.punctuation_changes[ch] += 1  # Log the conversion.
                replacements.append(PUNCT_MAP_EN[ch])  # Replace with ASCII punctuation.
            else:  # Otherwise keep as is.
                replacements.append(ch)  # Append unchanged character.
        text = "".join(replacements)  # Construct new string.
    # Normalise ellipsis variations to the single Unicode ellipsis.
    for variant in ELLIPSIS_VARIANTS:  # Check all known variant strings.
        if variant in text:  # Only perform replacement when variant is present.
            text = text.replace(variant, "…")  # Replace variant with canonical ellipsis.
            stage.stats.punctuation_changes["ellipsis"] += 1  # Record that ellipsis was normalised.
    # Replace various dash characters with the standard Chinese long dash.
    for variant in DASH_VARIANTS:  # Iterate through dash alternatives.
        if variant in text and variant != "——":  # Avoid remapping the correct dash.
            text = text.replace(variant, "——")  # Substitute with canonical dash.
            stage.stats.punctuation_changes["dash"] += 1  # Log dash replacement.
    # Collapse repeated punctuation marks to a single instance to avoid duplicates.
    for mark in "，。？！：；…——":  # Iterate over key punctuation to deduplicate.
        repeated = mark * 2  # Construct double mark string for search.
        while repeated in text:  # Continue collapsing until no duplicates remain.
            text = text.replace(repeated, mark)  # Replace duplicates with single mark.
            stage.stats.punctuation_changes["dedup"] += 1  # Count each deduplication.
    return StageResult(text=text, stats=stage.stats)  # Return updated stage result.

# Adjust spacing, remove invisible characters, and enforce spacing rules.
def normalize_spaces(stage: StageResult, keep_english_case: bool, number_style: str) -> StageResult:  # Clean whitespace and digits.
    """Clean whitespace and enforce spacing around CJK and ASCII text."""
    text = stage.text  # Start with incoming text.
    for inv in INVISIBLE_CHARS:  # Remove each invisible character variant.
        if inv in text:  # Only act when the character is present.
            replacement = "" if inv != "\u00a0" else " "  # Convert NBSP to space but drop others entirely.
            text = text.replace(inv, replacement)  # Apply replacement.
            stage.stats.whitespace_events["invisible"] += 1  # Record cleanup action.
    text = text.replace("\r\n", "\n").replace("\r", "\n")  # Normalise Windows line endings to Unix style.
    stage.stats.whitespace_events["linebreak_norm"] += 1  # Log newline normalisation.
    # Convert full-width digits to half-width when requested.
    if number_style == "half":  # Only adjust numbers for half-width preference.
        converted_chars = []  # Prepare buffer for processed characters.
        for ch in text:  # Iterate across characters.
            codepoint = ord(ch)  # Obtain Unicode code point.
            if 0xFF10 <= codepoint <= 0xFF19:  # Identify full-width digits ０-９.
                converted_chars.append(chr(codepoint - 0xFF10 + ord("0")))  # Map to ASCII digit.
                stage.stats.whitespace_events["digits"] += 1  # Count conversion.
            else:  # Leave other characters untouched for this pass.
                converted_chars.append(ch)  # Append original character.
        text = "".join(converted_chars)  # Join characters back together.
    elif number_style == "full":  # Support optional conversion to full-width digits.
        converted_chars = []  # Buffer for result.
        for ch in text:  # Iterate characters.
            if "0" <= ch <= "9":  # Identify ASCII digits.
                converted_chars.append(chr(ord(ch) - ord("0") + 0xFF10))  # Convert to full-width digit.
                stage.stats.whitespace_events["digits"] += 1  # Record conversion.
            else:  # Keep other characters intact.
                converted_chars.append(ch)  # Append original character.
        text = "".join(converted_chars)  # Combine result.
    # Optionally lowercase ASCII text when requested.
    if not keep_english_case:  # Only modify when lowercasing is desired.
        text = text.lower()  # Apply lowercasing to entire string.
        stage.stats.whitespace_events["case"] += 1  # Mark lowercase transformation.
    # Collapse multiple spaces into a single space globally.
    while "  " in text:  # Continue compressing until no double spaces remain.
        text = text.replace("  ", " ")  # Replace double spaces with single space.
        stage.stats.whitespace_events["collapse_spaces"] += 1  # Count each collapse iteration.
    # Remove spaces between consecutive CJK characters.
    compact = []  # Prepare buffer for building cleaned string.
    for index, ch in enumerate(text):  # Enumerate to inspect neighbours.
        if ch == " " and index > 0 and index + 1 < len(text):  # Focus on interior spaces.
            prev_char = text[index - 1]  # Fetch preceding character.
            next_char = text[index + 1]  # Fetch following character.
            if is_cjk(prev_char) and is_cjk(next_char):  # Check if both sides are CJK characters.
                stage.stats.whitespace_events["cjk_space_removed"] += 1  # Count removal event.
                continue  # Skip appending this space.
        compact.append(ch)  # Append character when not skipped.
    text = "".join(compact)  # Compose final text after CJK space cleanup.
    # Ensure only single space between Chinese and ASCII/number sequences.
    text = enforce_mixed_spacing(text, stage.stats)  # Delegate to helper for clarity.
    # Strip trailing spaces on each line.
    lines = []  # Buffer for processed lines.
    for raw_line in text.split("\n"):  # Process each line individually.
        stripped = raw_line.rstrip(" ")  # Remove trailing spaces explicitly.
        if stripped != raw_line:  # Detect actual trimming.
            stage.stats.whitespace_events["line_trail"] += 1  # Count trimmed lines.
        lines.append(stripped)  # Append processed line.
    return StageResult(text="\n".join(lines), stats=stage.stats)  # Reassemble text with cleaned lines.

# Helper to ensure mixed Chinese-ASCII spacing remains consistent.
def enforce_mixed_spacing(text: str, stats: NormalizationStats) -> str:  # Constrain spacing between mixed scripts.
    """Guarantee at most one space between CJK and ASCII/number segments."""
    result = []  # Output buffer for constructing new string.
    index = 0  # Track position while iterating.
    while index < len(text):  # Iterate sequentially through the string.
        ch = text[index]  # Current character under inspection.
        if ch == " ":  # Only special-case spaces.
            prev_char = text[index - 1] if index > 0 else ""  # Look backward safely.
            next_char = text[index + 1] if index + 1 < len(text) else ""  # Look forward safely.
            if prev_char and next_char:  # Ensure both neighbours exist.
                if (is_cjk(prev_char) and is_ascii_or_digit(next_char)) or (is_ascii_or_digit(prev_char) and is_cjk(next_char)):  # Mixed boundary.
                    result.append(" ")  # Keep exactly one space at mixed boundary.
                    index += 1  # Advance pointer by one character.
                    continue  # Skip further processing for this character.
                if is_ascii_or_digit(prev_char) and is_ascii_or_digit(next_char):  # Preserve spaces within ASCII sequences.
                    result.append(" ")  # Retain single space between ASCII words.
                    index += 1  # Advance pointer.
                    continue  # Skip removal for this space.
                if is_ascii_or_digit(prev_char) and next_char == " ":  # Double space after ASCII sequences.
                    stats.whitespace_events["ascii_space_trim"] += 1  # Record trimming of redundant space.
                    index += 1  # Skip redundant space entirely.
                    continue  # Continue without appending.
            stats.whitespace_events["space_removed"] += 1  # Track general space removal.
            index += 1  # Move past the space.
            continue  # Do not append this space.
        result.append(ch)  # Append non-space characters verbatim.
        index += 1  # Advance pointer.
    return "".join(result)  # Return reconstructed string.

# Determine whether a character is part of the unified CJK blocks.
def is_cjk(ch: str) -> bool:  # Identify whether character belongs to CJK blocks.
    """Return True when character belongs to CJK ranges."""
    code = ord(ch)  # Convert to Unicode code point for range comparisons.
    return 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0x20000 <= code <= 0x2A6DF  # Cover basic, extension A, and part of B.

# Identify ASCII letters or digits to support spacing logic.
def is_ascii_or_digit(ch: str) -> bool:  # Recognise ASCII letters or digits for spacing logic.
    """Check if character is ASCII letter or digit."""
    return ("0" <= ch <= "9") or ("a" <= ch.lower() <= "z")  # Evaluate digits and case-insensitive alphabetic range.

# Merge soft-wrapped lines into paragraphs.
def reflow_lines(stage: StageResult) -> StageResult:  # Merge visually wrapped lines into paragraphs.
    """Collapse line breaks that only reflect visual wrapping."""
    lines = stage.text.split("\n")  # Break into individual lines for processing.
    result_lines: List[str] = []  # Accumulate final lines.
    buffer = ""  # Track ongoing paragraph assembly.
    for line in lines:  # Inspect each line sequentially.
        stripped = line.strip()  # Remove leading and trailing spaces for decision-making.
        if not stripped:  # Blank lines signal paragraph boundaries.
            if buffer:  # Flush buffer when content exists.
                result_lines.append(buffer)  # Commit buffered paragraph.
                buffer = ""  # Reset buffer for next paragraph.
            result_lines.append("")  # Preserve blank line explicitly.
            continue  # Proceed to next input line.
        if not buffer:  # If buffer empty start new paragraph.
            buffer = stripped  # Seed buffer with current line content.
            continue  # Move to next line.
        last_char = buffer[-1]  # Last character in current buffer for punctuation check.
        first_char = stripped[0]  # First character of next line.
        if last_char in SENTENCE_ENDINGS or first_char in SENTENCE_ENDINGS:  # Avoid merging when sentence boundary exists.
            result_lines.append(buffer)  # Finalise current buffer.
            buffer = stripped  # Start new buffer with current line.
            continue  # Evaluate next line.
        if first_char in "·•*-" or stripped[:2] in {"一、", "1."}:  # Avoid merging lists or headings.
            result_lines.append(buffer)  # Finalise existing paragraph.
            buffer = stripped  # Start new paragraph.
            continue  # Skip merging for list-style lines.
        joiner = determine_joiner(buffer[-1], stripped[0])  # Choose whether to insert space when merging.
        buffer = buffer + joiner + stripped  # Append current line to buffer using joiner.
    if buffer:  # After processing all lines flush remaining buffer.
        result_lines.append(buffer)  # Add final paragraph to results.
    text = "\n".join(result_lines)  # Reconstruct text using newline separators.
    stage.stats.whitespace_events["reflow"] += 1  # Log that reflow took place.
    return StageResult(text=text, stats=stage.stats)  # Return updated stage result.

# Determine joiner string between merged lines.
def determine_joiner(prev_char: str, next_char: str) -> str:  # Choose joiner character when merging lines.
    """Return an appropriate joiner when merging lines."""
    if is_ascii_or_digit(prev_char) and is_ascii_or_digit(next_char):  # ASCII sequences need a space.
        return " "  # Insert space for readability.
    if prev_char in "([{“「『" or next_char in ")]},，。！？：；":  # Avoid extra spacing around brackets and punctuation.
        return ""  # Join without inserting spaces.
    return ""  # Default to no space for CJK continuity.

# Strip front or back matter heuristically using keyword matching.
def strip_front_matter(stage: StageResult, lang: str, enabled: bool) -> StageResult:  # Remove suspected front/back matter.
    """Remove non-body sections such as copyright pages when requested."""
    if not enabled:  # Skip operation if feature disabled.
        return stage  # Return input unchanged.
    if lang != "zh":  # Currently only tuned for Chinese heuristics.
        return stage  # Leave text intact for other languages.
    lines = stage.text.split("\n")  # Inspect text line by line.
    kept_lines: List[str] = []  # Store lines that survive filtering.
    removed_snippets: List[str] = []  # Record removed content for report inclusion.
    for idx, line in enumerate(lines):  # Iterate with index for positional rules.
        stripped = line.strip()  # Remove surrounding whitespace for analysis.
        if not stripped:  # Preserve blank lines unconditionally.
            kept_lines.append(line)  # Append blank line to maintain separation.
            continue  # Move to next line.
        lower = stripped.lower()  # Provide case-insensitive matching support.
        if idx < 20:  # Focus early lines for front matter detection.
            if any(keyword.lower() in lower for keyword in FRONT_MATTER_KEYWORDS):  # Keyword hit indicates front matter.
                removed_snippets.append(stripped)  # Store removed line snippet.
                stage.stats.front_matter_removed.append(stripped)  # Track removal for report.
                continue  # Skip adding to output lines.
        if idx > len(lines) - 20:  # Inspect tail lines for back matter.
            if any(keyword.lower() in lower for keyword in FRONT_MATTER_KEYWORDS):  # Same keyword logic for tail.
                removed_snippets.append(stripped)  # Record removal.
                stage.stats.front_matter_removed.append(stripped)  # Track removal for report.
                continue  # Skip this line.
        kept_lines.append(line)  # Keep lines not identified as front/back matter.
    if removed_snippets:  # Add warning when removals happened to alert reviewer.
        stage.stats.warnings.append(f"Removed {len(removed_snippets)} lines of suspected front/back matter.")  # Compose warning message.
    return StageResult(text="\n".join(kept_lines), stats=stage.stats)  # Return filtered text with stats.

# Load ASR words and compute comparative metrics.
def compare_with_words(text: str, words_path: Optional[Path], punct_style: str) -> ComparisonMetrics:  # Build diff metrics against ASR words.
    """Compute difference metrics between normalised text and ASR words."""
    if words_path is None:  # Allow running without words reference.
        empty_counter = Counter()  # Prepare empty counter for missing data scenario.
        return ComparisonMetrics(empty_counter, empty_counter, 0.0, [], 0.0)  # Return neutral metrics.
    from onepass.asr_loader import load_words  # Import lazily to avoid circular dependencies.
    words = load_words(Path(words_path))  # Load structured word entries.
    asr_text = "".join(word.text for word in words)  # Concatenate ASR word texts into single string.
    if punct_style == "zh":  # Normalise punctuation style to match processed text.
        asr_text = "".join(PUNCT_MAP_ZH.get(ch, ch) for ch in asr_text)  # Map ASCII punctuation to Chinese equivalents.
    elif punct_style == "en":  # Optionally convert to ASCII punctuation.
        asr_text = "".join(PUNCT_MAP_EN.get(ch, ch) for ch in asr_text)  # Map Chinese punctuation back to ASCII.
    filtered_text, mapping = strip_whitespace_with_index(text)  # Remove whitespace while tracking index mapping.
    filtered_words, _ = strip_whitespace_with_index(asr_text)  # Remove whitespace from ASR text for fair comparison.
    text_counter = Counter(ch for ch in filtered_text if is_relevant_char(ch))  # Count relevant characters in text.
    words_counter = Counter(ch for ch in filtered_words if is_relevant_char(ch))  # Count relevant characters in words.
    text_only = Counter({ch: cnt for ch, cnt in text_counter.items() if ch not in words_counter})  # Characters absent from ASR.
    words_only = Counter({ch: cnt for ch, cnt in words_counter.items() if ch not in text_counter})  # Characters absent from text.
    punctuation_overlap = compute_punctuation_overlap(filtered_text, filtered_words)  # Measure punctuation consistency.
    ngram_mismatch_entries = collect_ngram_mismatches(filtered_text, filtered_words, mapping, text)  # Evaluate n-gram gaps.
    align_score = estimate_align_score(filtered_text, filtered_words, punctuation_overlap)  # Estimate overall friendliness.
    return ComparisonMetrics(text_only, words_only, punctuation_overlap, ngram_mismatch_entries, align_score)  # Package metrics.

# Remove whitespace from text while keeping original index mapping.
def strip_whitespace_with_index(text: str) -> Tuple[str, List[int]]:  # Remove whitespace and track index mapping.
    """Strip whitespace characters and keep mapping to original indices."""
    cleaned_chars = []  # Characters without whitespace.
    index_map: List[int] = []  # Map from cleaned index to original index.
    for idx, ch in enumerate(text):  # Iterate across characters.
        if ch.isspace():  # Skip whitespace characters.
            continue  # Do not include whitespace.
        cleaned_chars.append(ch)  # Append visible character.
        index_map.append(idx)  # Record source index.
    return "".join(cleaned_chars), index_map  # Return compact string and mapping list.

# Determine characters relevant for set difference analysis.
def is_relevant_char(ch: str) -> bool:  # Decide if character participates in diff analysis.
    """Check if character should be considered in diff metrics."""
    return is_cjk(ch) or ch in "，。？！：；——…"  # Focus on CJK and major punctuation marks.

# Compute punctuation overlap ratio.
def compute_punctuation_overlap(text: str, words: str) -> float:  # Measure punctuation overlap ratio.
    """Calculate percentage of punctuation characters that overlap between text and words."""
    text_punct = Counter(ch for ch in text if ch in "，。？！：；——…")  # Count punctuation in text.
    word_punct = Counter(ch for ch in words if ch in "，。？！：；——…")  # Count punctuation in words.
    if not text_punct:  # Avoid division by zero when text lacks punctuation.
        return 1.0  # Treat as full overlap when there is nothing to compare.
    matched = sum(min(text_punct[ch], word_punct.get(ch, 0)) for ch in text_punct)  # Count matched punctuation occurrences.
    total = sum(text_punct.values())  # Total punctuation occurrences in text.
    return matched / total  # Return ratio between 0 and 1.

# Collect n-gram mismatches and locate them in the original text.
def collect_ngram_mismatches(text: str, words: str, index_map: List[int], original_text: str) -> List[Tuple[str, int, int, str]]:  # Identify text n-grams missing from ASR words.
    """Find n-grams present in text but absent in ASR words."""
    mismatches: List[Tuple[str, int, int, str]] = []  # Container for mismatch entries.
    for n in (2, 3):  # Analyse bigrams and trigrams.
        text_ngrams = Counter(text[i : i + n] for i in range(len(text) - n + 1))  # Count text n-grams.
        word_ngram_set = {words[i : i + n] for i in range(len(words) - n + 1)}  # Build ASR n-gram set for fast lookup.
        candidates = [(gram, count) for gram, count in text_ngrams.items() if gram not in word_ngram_set]  # Filter mismatches.
        candidates.sort(key=lambda item: item[1], reverse=True)  # Sort by frequency descending.
        for gram, count in candidates[:20]:  # Limit to top 20 mismatches.
            first_index = text.find(gram)  # Locate first occurrence in cleaned text.
            if first_index == -1:  # Fallback if not found (should not happen).
                continue  # Skip this gram.
            original_index = index_map[first_index] if first_index < len(index_map) else 0  # Map back to original text index.
            line_no, snippet = locate_in_original(original_text, original_index)  # Retrieve line number and context snippet.
            mismatches.append((gram, n, line_no, snippet.strip()))  # Append mismatch entry.
    return mismatches  # Return collected mismatches.

# Locate the original line and snippet for a given index.
def locate_in_original(text: str, index: int) -> Tuple[int, str]:  # Map compact index back to original line.
    """Return (line number, line text) for the specified character index."""
    current = 0  # Track cumulative character count.
    for line_no, line in enumerate(text.split("\n"), start=1):  # Iterate lines with numbering.
        line_length = len(line) + 1  # Account for newline character.
        if index < current + line_length:  # Determine if target index falls within current line.
            return line_no, line  # Return the matching line.
        current += line_length  # Advance cumulative count.
    return len(text.split("\n")), text.split("\n")[-1] if text else ""  # Fallback to last line when index beyond range.

# Estimate alignment friendliness score based on coverage metrics.
def estimate_align_score(text: str, words: str, punctuation_overlap: float) -> float:  # Score expected alignment quality.
    """Estimate how well the normalised text should align with ASR words."""
    if not text:  # Handle edge case where text is empty.
        return 0.0  # No content implies zero score.
    text_chars = Counter(text)  # Count characters from text.
    word_chars = Counter(words)  # Count characters from ASR.
    matched_chars = sum(min(text_chars[ch], word_chars.get(ch, 0)) for ch in text_chars)  # Determine shared character count.
    char_coverage = matched_chars / len(text)  # Compute character coverage ratio.
    bigrams = len(text) - 1 if len(text) > 1 else 1  # Avoid division by zero for bigram coverage denominator.
    words_bigrams = {words[i : i + 2] for i in range(len(words) - 1)}  # Build ASR bigram set.
    matched_bigrams = sum(1 for i in range(len(text) - 1) if text[i : i + 2] in words_bigrams)  # Count matched bigrams.
    bigram_coverage = matched_bigrams / bigrams  # Ratio of matched bigrams.
    score = (char_coverage + punctuation_overlap + bigram_coverage) / 3  # Average the coverage metrics.
    return round(score * 100, 2)  # Express as percentage with two decimal places.

# Generate markdown report summarising normalisation and comparison results.
def write_report(output_path: Path, orig_text: str, norm_text: str, stats: NormalizationStats, metrics: ComparisonMetrics, compat_map: Mapping[str, str]) -> None:  # Emit markdown diff report.
    """Write a diff-style markdown report with diagnostic information."""
    lines: List[str] = []  # Collect report lines before writing.
    lines.append(f"# Normalization Report for {output_path.stem}")  # Report title includes stem.
    lines.append("")  # Insert blank line for readability.
    lines.append(f"- Original length: {len(orig_text)} characters")  # Record original character count.
    lines.append(f"- Normalized length: {len(norm_text)} characters")  # Record normalised character count.
    lines.append(f"- Estimated alignment score: {metrics.align_score}/100")  # Include computed score.
    if stats.compat_replacements:  # Only include when replacements occurred.
        lines.append("")  # Separate section.
        lines.append("## Compatibility Replacements")  # Section heading.
        for ch, count in stats.compat_replacements.most_common():  # Iterate replacements by frequency.
            replacement = compat_map.get(ch, "?")  # Look up mapped target for clarity.
            lines.append(f"- `{ch}` → `{replacement}` × {count}")  # Show mapping details.
    if stats.compat_missing:  # Report unknown compatibility characters.
        lines.append("")  # Insert spacing before section.
        lines.append("## Unmapped Compatibility Characters")  # Section heading for missing mappings.
        for ch, count in stats.compat_missing.most_common():  # Iterate through unmapped characters.
            suggestion = unicodedata.name(ch, "unknown")  # Provide Unicode name as hint.
            lines.append(f"- `{ch}` (U+{ord(ch):04X}, {suggestion}) × {count} — add to compat map")  # Prompt addition to mapping file.
    if stats.punctuation_changes:  # Include punctuation statistics when available.
        lines.append("")  # Add blank line separator.
        lines.append("## Punctuation Adjustments")  # Heading for punctuation stats.
        for key, count in stats.punctuation_changes.most_common():  # Iterate change counters.
            lines.append(f"- `{key}` adjustments: {count}")  # List adjustment counts per key.
    if stats.whitespace_events:  # Include whitespace metrics.
        lines.append("")  # Insert separator before section.
        lines.append("## Whitespace and Formatting")  # Heading for whitespace section.
        for key, count in stats.whitespace_events.most_common():  # Iterate whitespace counters.
            lines.append(f"- {key}: {count}")  # Present counter values.
    if stats.front_matter_removed:  # Display removed front/back matter when present.
        lines.append("")  # Separate section visually.
        lines.append("## Removed Front/Back Matter Samples")  # Heading for sample snippet list.
        for snippet in stats.front_matter_removed[:10]:  # Limit to first ten snippets.
            lines.append(f"> {snippet}")  # Quote removed line for review.
    if metrics.text_only_chars or metrics.words_only_chars:  # Report character set differences when non-empty.
        lines.append("")  # Add blank line.
        lines.append("## Character Set Differences")  # Section heading.
        if metrics.text_only_chars:  # Only render when there are text-only characters.
            lines.append("- Present only in normalized text:")  # Introductory bullet.
            for ch, count in metrics.text_only_chars.most_common():  # Iterate characters.
                lines.append(f"  - `{ch}` × {count}")  # Display character and count.
        if metrics.words_only_chars:  # Render ASR-only characters when present.
            lines.append("- Present only in ASR words:")  # Introductory bullet for ASR list.
            for ch, count in metrics.words_only_chars.most_common():  # Iterate characters.
                lines.append(f"  - `{ch}` × {count}")  # Display ASR-only entry.
    if metrics.ngram_mismatches:  # Include n-gram mismatch section when mismatches exist.
        lines.append("")  # Separate from previous content.
        lines.append("## Top N-gram Mismatches")  # Heading for mismatch list.
        for gram, length, line_no, snippet in metrics.ngram_mismatches[:20]:  # Limit to top twenty entries.
            lines.append(f"- `{gram}` (n={length}) at line {line_no}: {snippet}")  # Provide mismatch details.
    if stats.warnings:  # Display accumulated warnings if any.
        lines.append("")  # Insert spacing before warnings.
        lines.append("## Warnings")  # Heading for warnings.
        for warning in stats.warnings:  # Iterate warnings collection.
            lines.append(f"- {warning}")  # Include warning text.
    output_path.parent.mkdir(parents=True, exist_ok=True)  # Ensure destination directory exists.
    output_path.write_text("\n".join(lines), encoding="utf-8")  # Write markdown report to disk.

# Process a single file pair.
def run_file(orig_path: Path, words_path: Optional[Path], out_path: Path, report_path: Optional[Path], *, lang: str, strip_frontmatter: bool, punct_style: str, number_style: str, keep_english_case: bool, to_simplified: bool, compat_map: Mapping[str, str]) -> None:  # Execute pipeline for single transcript.
    """Run full normalisation pipeline for one transcript."""
    stats = NormalizationStats()  # Instantiate statistics accumulator.
    stage = load_text(orig_path, stats)  # Load original text.
    orig_text = stage.text  # Preserve raw text for reporting.
    stage = apply_nfkc(stage)  # Apply NFKC normalisation.
    stage = map_compat_chars(stage, compat_map)  # Replace compatibility characters.
    stage = apply_opencc(stage, to_simplified)  # Optionally convert script variant.
    stage = normalize_punct(stage, style=punct_style)  # Harmonise punctuation.
    stage = normalize_spaces(stage, keep_english_case=keep_english_case, number_style=number_style)  # Tidy spacing.
    stage = reflow_lines(stage)  # Merge soft-wrapped lines.
    stage = strip_front_matter(stage, lang=lang, enabled=strip_frontmatter)  # Remove front/back matter.
    norm_text = stage.text  # Extract final normalised text.
    out_path.parent.mkdir(parents=True, exist_ok=True)  # Ensure output directory exists.
    out_path.write_text(norm_text, encoding="utf-8")  # Persist normalised text to disk.
    metrics = compare_with_words(norm_text, words_path, punct_style)  # Compute comparison metrics.
    if report_path is not None:  # Only write report when path provided.
        write_report(report_path, orig_text, norm_text, stats, metrics, compat_map)  # Generate markdown report including map info.

# Resolve matching words.json path for a given transcript file.
def find_words_path(orig_path: Path, words_dir: Optional[Path]) -> Optional[Path]:  # Locate matching words JSON file.
    """Attempt to locate ASR words JSON corresponding to the transcript."""
    if words_dir is None:  # Return None when no directory provided.
        return None  # Without words directory there is no match.
    stem = orig_path.stem  # Extract stem from original filename.
    candidate = words_dir / f"{stem}.words.json"  # Compose expected words path.
    if candidate.exists():  # Return candidate when file exists.
        return candidate  # Provide matched path.
    LOGGER.warning("Words JSON not found for %s", orig_path)  # Log warning for missing file.
    return None  # Indicate absence by returning None.

# Process all files within directories.
def run_batch(orig_dir: Path, words_dir: Optional[Path], out_dir: Path, report_dir: Optional[Path], *, lang: str, strip_frontmatter: bool, punct_style: str, number_style: str, keep_english_case: bool, to_simplified: bool, compat_map: Mapping[str, str]) -> None:  # Process directory of transcripts.
    """Iterate through directory and normalise each transcript."""
    for orig_path in sorted(orig_dir.glob("*.txt")):  # Iterate deterministic order over TXT files.
        words_path = find_words_path(orig_path, words_dir)  # Locate matching words JSON if available.
        out_path = out_dir / f"{orig_path.stem}.norm.txt"  # Determine output file path.
        report_path = (report_dir / f"{orig_path.stem}.norm.diff.md") if report_dir is not None else None  # Compose report path.
        run_file(orig_path, words_path, out_path, report_path, lang=lang, strip_frontmatter=strip_frontmatter, punct_style=punct_style, number_style=number_style, keep_english_case=keep_english_case, to_simplified=to_simplified, compat_map=compat_map)  # Execute pipeline.

# Build argument parser for CLI usage.
def build_parser() -> argparse.ArgumentParser:  # Construct CLI argument parser.
    """Construct the command-line argument parser."""
    parser = argparse.ArgumentParser(description="Normalise original transcripts for ASR alignment.")  # Instantiate parser with description.
    parser.add_argument("--orig", type=Path, help="Path to original transcript text file.")  # Single file input path.
    parser.add_argument("--words", type=Path, help="Path to ASR words JSON file.")  # Optional words JSON path.
    parser.add_argument("--out", type=Path, help="Destination path for normalised text output.")  # Single output path.
    parser.add_argument("--report", type=Path, help="Destination path for markdown report.")  # Report file path.
    parser.add_argument("--orig-dir", type=Path, help="Directory containing original transcript TXT files.")  # Batch mode directory.
    parser.add_argument("--words-dir", type=Path, help="Directory containing ASR words JSON files.")  # Batch words directory.
    parser.add_argument("--out-dir", type=Path, help="Directory to store normalised outputs.")  # Batch output directory.
    parser.add_argument("--report-dir", type=Path, help="Directory for generated reports.")  # Batch report directory.
    parser.add_argument("--lang", default="zh", help="Language code for heuristics (default: zh).")  # Language flag.
    parser.add_argument("--strip-frontmatter", default="false", help="Whether to strip front/back matter (true/false).")  # Front matter toggle.
    parser.add_argument("--punct-style", default="zh", choices=["zh", "en"], help="Punctuation style to apply.")  # Punctuation choice.
    parser.add_argument("--number-style", default="half", choices=["half", "full", "keep"], help="Digit width handling strategy.")  # Number style.
    parser.add_argument("--keep-english-case", default="true", help="Preserve original ASCII letter casing.")  # Case preservation flag.
    parser.add_argument("--to-simplified", default="false", help="Convert traditional text to simplified using OpenCC if available.")  # Simplification flag.
    parser.add_argument("--compat-map", type=Path, default=Path("config/compat_map_zh.json"), help="Path to compatibility map JSON.")  # Allow custom mapping file.
    return parser  # Return configured parser.

# Entry point for command-line execution.
def main(argv: Optional[Sequence[str]] = None) -> None:  # Entry point for CLI execution.
    """Parse arguments and execute normalisation process."""
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")  # Configure default logging output.
    parser = build_parser()  # Construct argument parser.
    args = parser.parse_args(argv)  # Parse provided arguments.
    strip_flag = parse_bool(args.strip_frontmatter)  # Interpret strip front matter flag.
    keep_case_flag = parse_bool(args.keep_english_case)  # Interpret case flag.
    to_simplified_flag = parse_bool(args.to_simplified)  # Interpret OpenCC flag.
    words_path = args.words if args.words and args.words.exists() else None  # Validate optional words path.
    words_dir = args.words_dir if args.words_dir and args.words_dir.exists() else None  # Validate optional words directory.
    compat_map = load_compat_map(args.compat_map)  # Load compatibility mapping table.
    number_style = args.number_style  # Start with provided number style.
    if args.orig and args.out:  # Detect single-file mode.
        run_file(args.orig, words_path, args.out, args.report, lang=args.lang, strip_frontmatter=strip_flag, punct_style=args.punct_style, number_style=number_style, keep_english_case=keep_case_flag, to_simplified=to_simplified_flag, compat_map=compat_map)  # Execute single-file pipeline.
        return  # Exit after single-file processing.
    if args.orig_dir and args.out_dir:  # Detect batch mode scenario.
        run_batch(args.orig_dir, words_dir, args.out_dir, args.report_dir, lang=args.lang, strip_frontmatter=strip_flag, punct_style=args.punct_style, number_style=number_style, keep_english_case=keep_case_flag, to_simplified=to_simplified_flag, compat_map=compat_map)  # Execute batch processing.
        return  # Exit after batch run.
    parser.error("Specify either --orig/--out for single file or --orig-dir/--out-dir for batch processing.")  # Show usage error when neither mode selected.

# Enable module execution via python -m onepass.normalize_original.
if __name__ == "__main__":  # Standard entry point guard.
    main()  # Invoke main with default argv.
