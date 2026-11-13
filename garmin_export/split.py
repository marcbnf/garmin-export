"""Split an export into several files under NotebookLM's 500K-word limit."""

import json
import logging
import re
from pathlib import Path
from typing import Any

from .output import Document, word_count

log = logging.getLogger(__name__)

SPLIT_WORD_LIMIT = 480_000  # under NotebookLM's 500K limit, with margin


def write_split(doc: Document, out_dir: Path, base_filename: str,
                section_names: list[str]) -> list[Path]:
    """Write the document as several files, each under the word limit.

    Splits at section boundaries (section names on their own line). Oversized
    sections (like Daily Health) get their JSON broken into date-range or
    item-count chunks. Sections are packed greedily into files.
    """
    full_text = doc.text()
    names = section_names + ["Errors During Export"]
    split_pattern = r'(?=\n(?:' + '|'.join(re.escape(n) for n in names) + r')\n)'
    parts = re.split(split_pattern, full_text)

    # First part is the file header (title, date, format, TOC)
    file_header = parts[0] if parts else ""
    header_words = word_count(file_header)

    # Break oversized sections, keep small ones as-is
    chunks: list[tuple[str, str]] = []
    for sec_text in parts[1:]:
        sec_name = sec_text.strip().split('\n')[0].strip()
        if word_count(sec_text) <= SPLIT_WORD_LIMIT * 0.85:
            chunks.append((sec_name, sec_text))
        else:
            chunks.extend(_split_oversized_section(doc, sec_text, sec_name))

    # Greedy bin-packing into files
    files: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_words = header_words
    for name, text in chunks:
        chunk_words = word_count(text)
        if current_words + chunk_words > SPLIT_WORD_LIMIT and current:
            files.append(current)
            current, current_words = [], header_words
        current.append((name, text))
        current_words += chunk_words
    if current:
        files.append(current)

    total = len(files)
    written = []
    for i, file_sections in enumerate(files, 1):
        header = (f"Garmin Connect Data Export -- Part {i} of {total}\n\n"
                  f"Sections in this file: {', '.join(n for n, _ in file_sections)}\n"
                  f"Upload all {total} parts to the same notebook for complete data.\n\n")
        content = header + "\n".join(text for _, text in file_sections)
        fname = base_filename.replace(".txt", f"_split_part{i}of{total}.txt")
        path = out_dir / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
        log.info(f"  Part {i}/{total}: {fname} ({word_count(content):,} words, "
                 f"{path.stat().st_size / 1024:.0f} KB)")
    return written


def _split_oversized_section(doc: Document, sec_text: str, sec_name: str) -> list[tuple[str, str]]:
    """Break one oversized section by splitting its JSON content.

    Dict-keyed JSON (daily health, hydration, nutrition) is split into
    date-range groups; array JSON (activities) by item count.
    """
    match = re.search(r'(Schema:[^\n]*\n)\s*(\{.*\}|\[.*\])', sec_text, re.DOTALL)
    if not match:
        return [(sec_name, sec_text)]
    schema_line, json_str = match.group(1), match.group(2)
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return [(sec_name, sec_text)]

    target_words = int(SPLIT_WORD_LIMIT * 0.8)
    is_dict = isinstance(data, dict)
    items = list(data.items()) if is_dict else data if isinstance(data, list) else []
    if not items:
        return [(sec_name, sec_text)]

    # Group items by actual word count
    groups: list[list] = []
    current: list = []
    current_words = 0
    for item in items:
        item_words = word_count(json.dumps(dict([item]) if is_dict else item,
                                           default=str, ensure_ascii=False))
        if current_words + item_words > target_words and current:
            groups.append(current)
            current, current_words = [], 0
        current.append(item)
        current_words += item_words
    if current:
        groups.append(current)

    results = []
    first_index = 1
    for idx, group in enumerate(groups, 1):
        if is_dict:
            span = f"{group[0][0]} to {group[-1][0]}"
            chunk: Any = dict(group)
        else:
            span = f"items {first_index}-{first_index + len(group) - 1}"
            first_index += len(group)
            chunk = group
        part_name = f"{sec_name} (Part {idx} of {len(groups)}: {span})"
        results.append((part_name, f"{part_name}\n\n{schema_line}\n{doc.json(chunk)}\n"))
    return results
