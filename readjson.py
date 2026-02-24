#!/bin/python

from pathlib import Path
import json
import os
import re
import sys
import time
from typing import Any, List, Optional, Sequence, Tuple

SRC_DIR = Path(__file__).resolve().parent
JSON_DIR = SRC_DIR / 'json'
EXTRACT_TEXT_DIR = SRC_DIR / 'extract' / 'text'
EXTRACT_SELECT_DIR = SRC_DIR / 'extract' / 'select'
JSON_LIST_PATH = SRC_DIR / 'jsonlist.txt'
LAST_EXTRACT_TIME_PATH = SRC_DIR / 'last_extract_time.txt'

PREFERRED_ZH_INDEXES = (3, 2)  # usually Simplified Chinese, then Traditional Chinese
ZH_RE = re.compile(r'[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]')
TEXT_MARKERS_TO_REMOVE = ('%fSourceHanSansCN-M;', '%f;')


def log(message: str) -> None:
    print(message, file = sys.stderr)


def clean_text_markers(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    for marker in TEXT_MARKERS_TO_REMOVE:
        text = text.replace(marker, '')
    return text


def format_scene(scene_data: Optional[List[Any]]) -> str:
    if scene_data is None:
        return '\n  null'

    formatted_items = []
    for index, value in enumerate(scene_data):
        dumped = json.dumps(value, ensure_ascii = False)
        formatted_items.append(dumped if index % 2 == 0 else '  ' + dumped)
    return '\n  [\n    ' + ',\n    '.join(formatted_items) + '\n  ]'


def build_text_output(text_payload: List[Optional[List[Any]]]) -> str:
    return '[' + ','.join(map(format_scene, text_payload)) + '\n]'


def ensure_scene_slot(container: List[Any], index: int) -> None:
    while len(container) <= index:
        container.append(None)


def is_language_item(item: Any) -> bool:
    return isinstance(item, list) and len(item) >= 2 and isinstance(item[1], str)


def find_language_list(text_entry: Any) -> Optional[List[List[Any]]]:
    if not isinstance(text_entry, list):
        return None

    for field in text_entry:
        if isinstance(field, list) and field and all(is_language_item(i) for i in field):
            return field
    return None


def pick_language_entry(language_list: Sequence[List[Any]]) -> Optional[List[Any]]:
    for index in PREFERRED_ZH_INDEXES:
        if index < len(language_list):
            item = language_list[index]
            if is_language_item(item) and item[1]:
                return item

    for item in language_list:
        if is_language_item(item) and item[1] and ZH_RE.search(item[1]):
            return item

    for item in reversed(language_list):
        if is_language_item(item) and item[1]:
            return item

    return None


def pick_fallback_character(text_entry: Sequence[Any], prefer_index1: bool = False) -> Optional[str]:
    order = (1, 0) if prefer_index1 else (0, 1)
    for index in order:
        if index < len(text_entry):
            value = text_entry[index]
            if isinstance(value, str) and value:
                return value
    return None


def extract_character_and_text(text_entry: Any) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(text_entry, list):
        return None, None

    language_list = find_language_list(text_entry)
    if language_list:
        selected = pick_language_entry(language_list)
        if selected is not None:
            character = selected[0] if len(selected) > 0 and isinstance(selected[0], str) else None
            if not character:
                character = pick_fallback_character(text_entry, prefer_index1 = False)
            return character, clean_text_markers(selected[1])

    # Compatibility fallback for older/plain formats.
    if len(text_entry) > 2 and isinstance(text_entry[2], str):
        return pick_fallback_character(text_entry, prefer_index1 = True), clean_text_markers(text_entry[2])
    if len(text_entry) > 1 and isinstance(text_entry[1], str):
        return pick_fallback_character(text_entry, prefer_index1 = True), clean_text_markers(text_entry[1])
    return pick_fallback_character(text_entry, prefer_index1 = True), None


def read_json_file_list(path: Path) -> List[str]:
    with path.open('rt', encoding = 'utf-8') as f:
        return [line for line in f.read().splitlines(False) if line]


def collect_modified_files(json_files: Sequence[str], last_extract_path: Path) -> List[str]:
    targets: List[str] = []
    has_last_extract_time = last_extract_path.exists()
    last_extract_mtime = last_extract_path.stat().st_mtime if has_last_extract_time else None

    for name in json_files:
        source = JSON_DIR / name
        if not source.exists():
            continue
        if not has_last_extract_time or source.stat().st_mtime > last_extract_mtime:
            targets.append(name)
    return targets


def write_extract_files(filename: str, user_text: List[Optional[List[Any]]], user_select: List[Any]) -> None:
    EXTRACT_TEXT_DIR.mkdir(parents = True, exist_ok = True)
    EXTRACT_SELECT_DIR.mkdir(parents = True, exist_ok = True)

    text_path = EXTRACT_TEXT_DIR / filename
    select_path = EXTRACT_SELECT_DIR / filename

    text_path.write_text(build_text_output(user_text), encoding = 'utf-8')
    with select_path.open('wt', encoding = 'utf-8') as f:
        json.dump(user_select, f, ensure_ascii = False, indent = 2)


def extract_file(filename: str) -> bool:
    source = JSON_DIR / filename
    with source.open('rt', encoding = 'utf-8') as f:
        origin_json = json.load(f)

    user_text: List[Optional[List[Any]]] = []
    user_select: List[Any] = []

    scenes = origin_json.get('scenes') if isinstance(origin_json, dict) else None
    if not isinstance(scenes, list):
        # Keep output files present so downstream scripts do not fail on missing files.
        write_extract_files(filename, user_text, user_select)
        log(filename + ' skipped (no scenes).')
        return True

    for scene_index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue

        scene_texts = scene.get('texts')
        if isinstance(scene_texts, list):
            ensure_scene_slot(user_text, scene_index)
            user_text[scene_index] = []
            for text_entry in scene_texts:
                character, text = extract_character_and_text(text_entry)
                user_text[scene_index].append(character)
                user_text[scene_index].append(text)

        if 'selects' in scene:
            ensure_scene_slot(user_select, scene_index)
            user_select[scene_index] = scene['selects']

    write_extract_files(filename, user_text, user_select)
    log(filename + ' extract DONE!')
    return True


def update_extract_time(path: Path) -> None:
    path.write_text(time.asctime(), encoding = 'utf-8')
    log('Extract time updated.')


def main() -> int:
    os.chdir(SRC_DIR)

    json_file_list = read_json_file_list(JSON_LIST_PATH)
    modified_files = collect_modified_files(json_file_list, LAST_EXTRACT_TIME_PATH)

    new_extract = False
    for filename in modified_files:
        if extract_file(filename):
            new_extract = True

    if not new_extract:
        log('No file updated, have a good day!')
        return 0

    update_extract_time(LAST_EXTRACT_TIME_PATH)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
