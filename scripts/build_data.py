from __future__ import annotations

import json
import math
import re
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = ROOT / "DM2024.pdf"
DATA_DIR = ROOT / "data"
OUTPUT_PATH = DATA_DIR / "index.json"
SELECTED_CHAPTER_NUMBER = 1


STOPWORDS = {
    "а",
    "без",
    "более",
    "бы",
    "был",
    "была",
    "были",
    "было",
    "быть",
    "в",
    "вам",
    "вас",
    "весь",
    "во",
    "вот",
    "все",
    "всего",
    "всех",
    "вы",
    "где",
    "да",
    "для",
    "до",
    "его",
    "ее",
    "если",
    "есть",
    "еще",
    "же",
    "за",
    "здесь",
    "и",
    "из",
    "или",
    "им",
    "их",
    "к",
    "как",
    "ко",
    "когда",
    "кто",
    "ли",
    "либо",
    "между",
    "мне",
    "может",
    "мы",
    "на",
    "над",
    "нам",
    "нас",
    "не",
    "него",
    "нее",
    "нет",
    "ни",
    "них",
    "но",
    "о",
    "об",
    "один",
    "он",
    "она",
    "они",
    "оно",
    "от",
    "по",
    "под",
    "при",
    "с",
    "со",
    "так",
    "также",
    "такие",
    "такой",
    "там",
    "то",
    "того",
    "тоже",
    "той",
    "только",
    "том",
    "ты",
    "у",
    "уже",
    "чего",
    "чем",
    "что",
    "чтобы",
    "это",
    "этой",
    "этом",
    "этот",
}

WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯA-Z0-9])")


def normalise_token(token: str) -> str:
    token = token.lower().replace("ё", "е")
    if token.startswith("множеств"):
        return "множеств"
    for suffix in (
        "иями",
        "ями",
        "ами",
        "ого",
        "ему",
        "ыми",
        "ими",
        "ием",
        "ией",
        "ой",
        "ый",
        "ий",
        "ая",
        "ое",
        "ые",
        "ых",
        "ую",
        "юю",
        "ом",
        "ем",
        "ам",
        "ям",
        "ах",
        "ях",
        "ов",
        "ев",
        "ей",
        "ия",
        "ие",
        "ии",
        "ию",
        "ью",
        "ом",
        "ем",
        "а",
        "я",
        "ы",
        "и",
        "у",
        "ю",
        "е",
    ):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> list[str]:
    text = re.sub(r"([a-zа-яё]{3,})-([a-zа-яё]{3,})", r"\1\2", text, flags=re.IGNORECASE)
    tokens = []
    for word in WORD_RE.findall(text.lower().replace("ё", "е")):
        if len(word) < 2 or word.isdigit() or word in STOPWORDS:
            continue
        tokens.append(normalise_token(word))
    return tokens


def decode_pdf_literal(raw: bytes) -> bytes:
    raw = raw.strip()
    if not raw.startswith(b"("):
        return raw
    out = bytearray()
    i = 1
    depth = 1
    while i < len(raw) and depth:
        c = raw[i]
        if c == 92:
            i += 1
            if i >= len(raw):
                break
            c = raw[i]
            if 48 <= c <= 55:
                octal = bytes([c])
                i += 1
                for _ in range(2):
                    if i < len(raw) and 48 <= raw[i] <= 55:
                        octal += bytes([raw[i]])
                        i += 1
                    else:
                        break
                out.append(int(octal, 8))
                continue
            escape_map = {
                ord("n"): 10,
                ord("r"): 13,
                ord("t"): 9,
                ord("b"): 8,
                ord("f"): 12,
                ord("("): 40,
                ord(")"): 41,
                ord("\\"): 92,
            }
            if c in escape_map:
                out.append(escape_map[c])
            elif c in (10, 13):
                pass
            else:
                out.append(c)
        elif c == 40:
            depth += 1
            out.append(c)
        elif c == 41:
            depth -= 1
            if depth:
                out.append(c)
        else:
            out.append(c)
        i += 1
    return bytes(out)


def decode_pdf_text_string(raw: bytes) -> str:
    if raw.strip().startswith(b"("):
        data = decode_pdf_literal(raw)
    elif raw.strip().startswith(b"<"):
        hex_text = re.sub(rb"\s+", b"", raw.strip().strip(b"<>"))
        data = bytes.fromhex(hex_text.decode("ascii"))
    else:
        data = raw.strip()
    if data.startswith(b"\xfe\xff"):
        return data[2:].decode("utf-16-be", "replace")
    if data.startswith(b"\xff\xfe"):
        return data[2:].decode("utf-16-le", "replace")
    return data.decode("utf-8", "replace")


def read_regular_token(data: bytes, i: int) -> tuple[str, int]:
    start = i
    delimiters = b" \t\r\n\f[]<>()/"
    while i < len(data) and data[i] not in delimiters:
        i += 1
    return data[start:i].decode("latin1", "replace"), i


def skip_ws(data: bytes, i: int) -> int:
    while i < len(data):
        if data[i] in b" \t\r\n\f\0":
            i += 1
            continue
        if data[i] == ord("%"):
            while i < len(data) and data[i] not in b"\r\n":
                i += 1
            continue
        break
    return i


def read_literal_token(data: bytes, i: int) -> tuple[dict[str, Any], int]:
    start = i
    i += 1
    depth = 1
    while i < len(data) and depth:
        c = data[i]
        if c == 92:
            i += 2
            continue
        if c == 40:
            depth += 1
        elif c == 41:
            depth -= 1
        i += 1
    return {"type": "string", "value": decode_pdf_literal(data[start:i])}, i


def read_hex_token(data: bytes, i: int) -> tuple[dict[str, Any] | str, int]:
    if i + 1 < len(data) and data[i + 1] == ord("<"):
        return "<<", i + 2
    start = i + 1
    i = start
    while i < len(data) and data[i] != ord(">"):
        i += 1
    hex_text = re.sub(rb"\s+", b"", data[start:i])
    if len(hex_text) % 2:
        hex_text += b"0"
    value = bytes.fromhex(hex_text.decode("ascii", "ignore")) if hex_text else b""
    return {"type": "string", "value": value}, i + 1


def read_name_token(data: bytes, i: int) -> tuple[dict[str, Any], int]:
    i += 1
    start = i
    delimiters = b" \t\r\n\f[]<>()/"
    while i < len(data) and data[i] not in delimiters:
        i += 1
    return {"type": "name", "value": data[start:i].decode("latin1", "replace")}, i


def read_array_token(data: bytes, i: int) -> tuple[dict[str, Any], int]:
    values: list[Any] = []
    i += 1
    while i < len(data):
        i = skip_ws(data, i)
        if i >= len(data):
            break
        if data[i] == ord("]"):
            return {"type": "array", "value": values}, i + 1
        token, i = read_token(data, i)
        values.append(token)
    return {"type": "array", "value": values}, i


def read_token(data: bytes, i: int) -> tuple[Any, int]:
    i = skip_ws(data, i)
    if i >= len(data):
        return None, i
    c = data[i]
    if c == ord("("):
        return read_literal_token(data, i)
    if c == ord("<"):
        return read_hex_token(data, i)
    if c == ord("["):
        return read_array_token(data, i)
    if c == ord("]"):
        return "]", i + 1
    if c == ord(">") and i + 1 < len(data) and data[i + 1] == ord(">"):
        return ">>", i + 2
    if c == ord("/"):
        return read_name_token(data, i)
    return read_regular_token(data, i)


@dataclass
class OutlineNode:
    obj: int
    title: str
    level: int
    page_index: int | None = None
    children: list["OutlineNode"] = field(default_factory=list)


class PdfDocument:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        self.objects = self._read_objects()
        self._stream_cache: dict[int, bytes] = {}
        self._cmap_cache: dict[int, dict[bytes, str]] = {}

    def _read_objects(self) -> dict[int, bytes]:
        objects: dict[int, bytes] = {}
        for match in re.finditer(rb"(?m)^(\d+)\s+(\d+)\s+obj\b", self.data):
            end = self.data.find(b"endobj", match.end())
            if end >= 0:
                objects[int(match.group(1))] = self.data[match.end() : end]
        return objects

    def ref(self, key: bytes, body: bytes) -> int | None:
        match = re.search(key + rb"\s+(\d+)\s+0\s+R", body)
        return int(match.group(1)) if match else None

    def refs_in_key_array(self, key: bytes, body: bytes) -> list[int]:
        match = re.search(key + rb"\s*\[(.*?)\]", body, re.S)
        if not match:
            return []
        return [int(num) for num in re.findall(rb"(\d+)\s+0\s+R", match.group(1))]

    def stream(self, obj_num: int) -> bytes:
        if obj_num in self._stream_cache:
            return self._stream_cache[obj_num]
        body = self.objects[obj_num]
        marker = body.find(b"stream")
        if marker < 0:
            self._stream_cache[obj_num] = body
            return body
        start = body.find(b"\n", marker)
        if start < 0:
            return b""
        start += 1
        end = body.rfind(b"endstream")
        chunk = body[start:end]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        elif chunk.endswith((b"\n", b"\r")):
            chunk = chunk[:-1]
        if b"/FlateDecode" in body:
            try:
                chunk = zlib.decompress(chunk)
            except zlib.error:
                chunk = b""
        self._stream_cache[obj_num] = chunk
        return chunk

    def catalog(self) -> int:
        for obj_num, body in self.objects.items():
            if b"/Type /Catalog" in body:
                return obj_num
        raise RuntimeError("PDF catalog was not found")

    def page_objects(self) -> list[int]:
        catalog_body = self.objects[self.catalog()]
        pages_root = self.ref(rb"/Pages", catalog_body)
        if pages_root is None:
            raise RuntimeError("Pages root was not found")
        pages: list[int] = []

        def walk(obj_num: int) -> None:
            body = self.objects[obj_num]
            if b"/Type /Page" in body and b"/Type /Pages" not in body:
                pages.append(obj_num)
                return
            for child in self.refs_in_key_array(rb"/Kids", body):
                walk(child)

        walk(pages_root)
        return pages

    def named_destinations(self) -> dict[str, int]:
        destinations: dict[str, int] = {}
        for body in self.objects.values():
            for name_raw, dest_obj_raw in re.findall(rb"\((Outline[^)]*)\)\s+(\d+)\s+0\s+R", body):
                name = name_raw.decode("latin1", "replace")
                dest_obj = int(dest_obj_raw)
                dest_body = self.objects.get(dest_obj, b"")
                page_match = re.search(rb"/D\s*\[\s*(\d+)\s+0\s+R", dest_body)
                if page_match:
                    destinations[name] = int(page_match.group(1))
        return destinations

    def outline(self, page_index_by_obj: dict[int, int]) -> list[OutlineNode]:
        root = self.ref(rb"/Outlines", self.objects[self.catalog()])
        if root is None:
            return []
        first = self.ref(rb"/First", self.objects[root])
        names = self.named_destinations()

        def title_for(obj_num: int) -> str:
            body = self.objects[obj_num]
            title_ref = self.ref(rb"/Title", body)
            if title_ref is None:
                return ""
            return decode_pdf_text_string(self.objects[title_ref]).strip()

        def page_for(obj_num: int) -> int | None:
            action = self.ref(rb"/A", self.objects[obj_num])
            if action is None:
                return None
            action_body = self.objects.get(action, b"")
            dest_match = re.search(rb"/D\s*(\([^)]*\))", action_body)
            if not dest_match:
                return None
            dest_name = decode_pdf_text_string(dest_match.group(1))
            page_obj = names.get(dest_name)
            return page_index_by_obj.get(page_obj) if page_obj is not None else None

        def walk_siblings(obj_num: int | None, level: int) -> list[OutlineNode]:
            nodes: list[OutlineNode] = []
            current = obj_num
            while current is not None:
                body = self.objects[current]
                child_first = self.ref(rb"/First", body)
                node = OutlineNode(
                    obj=current,
                    title=title_for(current),
                    level=level,
                    page_index=page_for(current),
                    children=walk_siblings(child_first, level + 1) if child_first else [],
                )
                nodes.append(node)
                current = self.ref(rb"/Next", body)
            return nodes

        return walk_siblings(first, 0)

    def font_maps_for_resources(self, resources_obj: int) -> dict[str, dict[bytes, str]]:
        body = self.objects.get(resources_obj, b"")
        font_maps: dict[str, dict[bytes, str]] = {}
        font_section = re.search(rb"/Font\s*<<(.*?)>>", body, re.S)
        if not font_section:
            return font_maps
        for name_raw, font_obj_raw in re.findall(rb"/([A-Za-z0-9]+)\s+(\d+)\s+0\s+R", font_section.group(1)):
            font_obj = int(font_obj_raw)
            cmap_ref = self.ref(rb"/ToUnicode", self.objects.get(font_obj, b""))
            if cmap_ref is None:
                continue
            font_maps[name_raw.decode("latin1")] = self.cmap(cmap_ref)
        return font_maps

    def cmap(self, obj_num: int) -> dict[bytes, str]:
        if obj_num in self._cmap_cache:
            return self._cmap_cache[obj_num]
        text = self.stream(obj_num).decode("latin1", "replace")
        mapping: dict[bytes, str] = {}

        for block in re.findall(r"beginbfchar(.*?)endbfchar", text, re.S):
            for source, target in re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
                mapping[bytes.fromhex(source)] = bytes.fromhex(target).decode("utf-16-be", "replace")

        for block in re.findall(r"beginbfrange(.*?)endbfrange", text, re.S):
            array_ranges = re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*\[(.*?)\]", block, re.S)
            for start_hex, end_hex, values in array_ranges:
                start = int(start_hex, 16)
                end = int(end_hex, 16)
                width = len(start_hex) // 2
                decoded_values = re.findall(r"<([0-9A-Fa-f]+)>", values)
                for offset, target_hex in enumerate(decoded_values):
                    if start + offset > end:
                        break
                    mapping[(start + offset).to_bytes(width, "big")] = bytes.fromhex(target_hex).decode(
                        "utf-16-be", "replace"
                    )
            simple_ranges = re.findall(r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block)
            for start_hex, end_hex, target_hex in simple_ranges:
                start = int(start_hex, 16)
                end = int(end_hex, 16)
                target = int(target_hex, 16)
                width = len(start_hex) // 2
                for code in range(start, end + 1):
                    mapping[code.to_bytes(width, "big")] = chr(target + code - start)

        self._cmap_cache[obj_num] = mapping
        return mapping

    def page_text(self, page_obj: int) -> str:
        page_body = self.objects[page_obj]
        resources = self.ref(rb"/Resources", page_body)
        font_maps = self.font_maps_for_resources(resources) if resources else {}
        content_refs = self.refs_in_key_array(rb"/Contents", page_body)
        single_content = self.ref(rb"/Contents", page_body)
        if not content_refs and single_content is not None:
            content_refs = [single_content]

        parts: list[str] = []
        for content_ref in content_refs:
            parts.append(self.extract_text_from_content(self.stream(content_ref), font_maps))
        return clean_page_text("\n".join(parts))

    def extract_text_from_content(self, content: bytes, font_maps: dict[str, dict[bytes, str]]) -> str:
        current_font = ""
        current_map: dict[bytes, str] = {}
        stack: list[Any] = []
        output: list[str] = []

        def decode_with_font(raw: bytes) -> str:
            if not current_map:
                return raw.decode("cp1251", "replace")
            result: list[str] = []
            max_len = max((len(key) for key in current_map), default=1)
            i = 0
            while i < len(raw):
                matched = False
                for size in range(max_len, 0, -1):
                    code = raw[i : i + size]
                    if code in current_map:
                        result.append(current_map[code])
                        i += size
                        matched = True
                        break
                if not matched:
                    result.append(raw[i : i + 1].decode("cp1251", "replace"))
                    i += 1
            return "".join(result)

        def show(value: Any) -> None:
            if isinstance(value, dict) and value.get("type") == "string":
                output.append(decode_with_font(value["value"]))
            elif isinstance(value, dict) and value.get("type") == "array":
                for item in value["value"]:
                    if isinstance(item, dict) and item.get("type") == "string":
                        output.append(decode_with_font(item["value"]))
                    elif isinstance(item, str):
                        try:
                            shift = float(item)
                        except ValueError:
                            continue
                        if shift <= -50 and output and not output[-1].endswith((" ", "\n")):
                            output.append(" ")

        i = 0
        while i < len(content):
            token, i = read_token(content, i)
            if token is None:
                break
            if isinstance(token, str):
                op = token
                if op == "Tf" and len(stack) >= 2:
                    font_operand = stack[-2]
                    if isinstance(font_operand, dict) and font_operand.get("type") == "name":
                        current_font = font_operand["value"]
                        current_map = font_maps.get(current_font, {})
                elif op in {"Tj", "'", '"'} and stack:
                    if op in {"'", '"'}:
                        output.append("\n")
                    show(stack[-1])
                elif op == "TJ" and stack:
                    show(stack[-1])
                elif op in {"Td", "TD"} and len(stack) >= 2:
                    try:
                        y = float(stack[-1])
                    except (TypeError, ValueError):
                        y = 0.0
                    if abs(y) > 2:
                        output.append("\n")
                elif op == "T*":
                    output.append("\n")
                elif op in {"ET", "BT"}:
                    output.append("\n")
                stack.clear()
            else:
                stack.append(token)
                if len(stack) > 12:
                    stack = stack[-12:]

        return "".join(output)


def clean_page_text(text: str) -> str:
    text = normalise_extracted_text(text)
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"\d+\s*/\s*1738", "", line).strip()
        if not line:
            continue
        if re.fullmatch(r"\d+\s*/\s*1738", line):
            continue
        if re.fullmatch(r"\d+", line):
            continue
        lines.append(line)
    return "\n".join(lines)


def normalise_extracted_text(text: str) -> str:
    replacements = {
        "\u0455": "«",
        "\u0457": "»",
        "\u0458": "е",
        "\u045c": "",
        "\x15": " - ",
        "\x16": " - ",
        "\uf6d4": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"(?<=[а-яёa-z])(?=[А-ЯЁA-Z])", " ", text)
    text = re.sub(
        r"\b(называется|называются|называют(?!ся)|означает|является|определяется)(?=[а-яё])",
        r"\1 ",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace("называют ся", "называются")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([«(])\s+", r"\1", text)
    text = re.sub(r"\s+([»)])", r"\1", text)
    return text


def clean_chapter_text(text: str) -> str:
    text = re.sub(r"\d+\s*/\s*1738", " ", text)
    lines = text.splitlines()
    cleaned: list[str] = []
    skip_algorithm = 0
    code_like = re.compile(
        r"^\s*(алгоритм\b|вход:|выход:|начало\b|конец\b|if\b|then\b|else\b|while\b|for\b|return\b|procedure\b|function\b)",
        re.IGNORECASE,
    )
    figure_like = re.compile(r"^\s*(рис\.|рисунок|таблица)\s*\d+", re.IGNORECASE)
    for line in lines:
        low = line.lower()
        if figure_like.match(line):
            continue
        if code_like.match(line) or re.match(r"^\s*\d+\.\s*(if|while|for|return|begin|end)\b", low):
            skip_algorithm = 8
            continue
        if skip_algorithm:
            if re.match(r"^\d+(\.\d+)+\.", line) or len(line) > 120 and line.endswith((".", "!", "?")):
                skip_algorithm = 0
            else:
                skip_algorithm -= 1
                continue
        cleaned.append(line)
    text = "\n".join(cleaned)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"(\(\d+/\d+\))(?=[А-ЯЁ])", r"\1. ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def flatten_outline(nodes: list[OutlineNode]) -> list[OutlineNode]:
    flat: list[OutlineNode] = []

    def walk(node_list: list[OutlineNode]) -> None:
        for node in node_list:
            flat.append(node)
            walk(node.children)

    walk(nodes)
    return flat


def clean_heading(title: str) -> str:
    title = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", title)
    return title.strip(" .")


def chapter_keywords(node: OutlineNode) -> list[str]:
    candidates = [clean_heading(node.title)]
    candidates.extend(clean_heading(child.title) for child in node.children)
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        pieces = [candidate]
        pieces.extend(re.split(r"\s+(?:и|или|с|в)\s+", candidate))
        for piece in pieces:
            piece = piece.strip(" .")
            if not piece:
                continue
            key = piece.lower().replace("ё", "е")
            if len(piece) > 2 and key not in seen:
                seen.add(key)
                result.append(piece)
    return result[:24]


def split_sentences(text: str) -> list[str]:
    compact = re.sub(r"\s+", " ", text).strip()
    rough = SENTENCE_SPLIT_RE.split(compact)
    sentences = []
    for sentence in rough:
        sentence = sentence.strip()
        if 45 <= len(sentence) <= 700:
            sentences.append(sentence)
    return sentences


def section_for_page(sections: list[OutlineNode], page_index: int) -> str:
    current = ""
    for section in sections:
        if section.page_index is not None and section.page_index <= page_index:
            current = section.title
    return current


def build_chunks(page_texts: list[dict[str, Any]], chapter_node: OutlineNode) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    buffer: list[str] = []
    buffer_pages: list[int] = []
    buffer_section = ""
    chunk_id = 0

    def flush() -> None:
        nonlocal chunk_id, buffer, buffer_pages, buffer_section
        if not buffer:
            return
        text = " ".join(buffer).strip()
        if len(text) < 120:
            buffer = []
            buffer_pages = []
            return
        chunks.append(
            {
                "id": f"chunk-{chunk_id}",
                "text": text,
                "page_start": min(buffer_pages) + 1,
                "page_end": max(buffer_pages) + 1,
                "section": buffer_section or chapter_node.title,
                "tokens": tokenize(text),
            }
        )
        chunk_id += 1
        buffer = []
        buffer_pages = []

    for page in page_texts:
        sentences = split_sentences(page["text"])
        for sentence in sentences:
            if not buffer_section:
                buffer_section = page["section"]
            if len(" ".join(buffer)) + len(sentence) > 950:
                flush()
                buffer_section = page["section"]
            buffer.append(sentence)
            buffer_pages.append(page["page_index"])
        if len(buffer) >= 6:
            flush()
            buffer_section = page["section"]
    flush()
    return chunks


def top_terms_from_text(text: str, keywords: list[str], limit: int = 18) -> list[str]:
    tokens = tokenize(text)
    counts = Counter(token for token in tokens if len(token) >= 4 and not token.isdigit())
    keyword_tokens = {token for keyword in keywords for token in tokenize(keyword)}
    for token in keyword_tokens:
        counts[token] += 8
    return [term for term, _ in counts.most_common(limit)]


def build_search_stats(chunks: list[dict[str, Any]], chapter_keywords_value: list[str]) -> dict[str, Any]:
    doc_freq: Counter[str] = Counter()
    chunk_lengths = []
    keyword_tokens = {token for keyword in chapter_keywords_value for token in tokenize(keyword)}
    for chunk in chunks:
        tokens = chunk["tokens"]
        chunk_lengths.append(len(tokens))
        doc_freq.update(set(tokens))
        chunk["term_counts"] = dict(Counter(tokens))
        chunk["keyword_hits"] = sorted(set(tokens) & keyword_tokens)
    avg_len = sum(chunk_lengths) / max(1, len(chunk_lengths))
    return {"doc_freq": dict(doc_freq), "avg_len": avg_len, "documents": len(chunks)}


def extract_definition_term(sentence: str) -> str | None:
    patterns = [
        r"называется\s+«?([а-яёa-z][а-яёa-z\s-]{2,55}?)»?(?:,|\s+если|\.|$)",
        r"называют\s+«?([а-яёa-z][а-яёa-z\s-]{2,55}?)»?(?:,|\s+если|\.|$)",
        r"определяется\s+как\s+«?([а-яёa-z][а-яёa-z\s-]{2,55}?)»?(?:,|\.|$)",
        r"«([^»]{3,55})»\s+называ",
    ]
    lowered = sentence.lower()
    for pattern in patterns:
        match = re.search(pattern, lowered, re.IGNORECASE)
        if not match:
            continue
        term = re.sub(r"\s+", " ", match.group(1)).strip(" .,-")
        words = term.split()
        if 1 <= len(words) <= 5:
            return term
    return None


def generate_trainer_items(chunks: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    used_sentences: set[str] = set()

    for keyword in keywords:
        if len(items) >= 18:
            break
        keyword_tokens = set(tokenize(keyword))
        if not keyword_tokens:
            continue
        best: tuple[int, str, dict[str, Any]] | None = None
        for chunk in chunks:
            for sentence in split_sentences(chunk["text"]):
                tokens = set(tokenize(sentence))
                overlap = len(tokens & keyword_tokens)
                definition_bonus = 2 if re.search(r"\b(называется|называют|определяется|если|есть|является)\b", sentence.lower()) else 0
                score = overlap * 3 + definition_bonus
                if score > 0 and (best is None or score > best[0]):
                    best = (score, sentence, chunk)
        if not best:
            continue
        _, sentence, chunk = best
        if sentence in used_sentences:
            continue
        used_sentences.add(sentence)
        expected_tokens = sorted(set(tokenize(sentence)))
        items.append(
            {
                "id": f"q-{len(items)}",
                "question": f"Объясните понятие «{keyword}».",
                "keyword": keyword,
                "answer_sentence": sentence,
                "expected_tokens": expected_tokens[:28],
                "section": chunk["section"],
                "page": chunk["page_start"],
            }
        )

    for chunk in chunks:
        for sentence in split_sentences(chunk["text"]):
            if len(items) >= 18:
                break
            term = extract_definition_term(sentence)
            if not term or sentence in used_sentences:
                continue
            if len(term.replace(" ", "")) > 32 or re.search(r"[a-z]\d|\d[a-z]|[;:=]", term, re.IGNORECASE):
                continue
            used_sentences.add(sentence)
            items.append(
                {
                    "id": f"q-{len(items)}",
                    "question": f"Что в главе называется «{term}»?",
                    "keyword": term,
                    "answer_sentence": sentence,
                    "expected_tokens": sorted(set(tokenize(sentence)))[:30],
                    "section": chunk["section"],
                    "page": chunk["page_start"],
                }
            )
    return items


def serialise_outline(nodes: list[OutlineNode]) -> list[dict[str, Any]]:
    return [
        {
            "title": node.title,
            "page": node.page_index + 1 if node.page_index is not None else None,
            "children": serialise_outline(node.children),
        }
        for node in nodes
    ]


def main() -> None:
    if not PDF_PATH.exists():
        raise SystemExit(f"PDF was not found: {PDF_PATH}")

    pdf = PdfDocument(PDF_PATH)
    pages = pdf.page_objects()
    page_index_by_obj = {obj: index for index, obj in enumerate(pages)}
    outline = pdf.outline(page_index_by_obj)
    top_level = outline
    chapters = [node for node in top_level if re.match(r"^\d+\.", node.title)]
    if not chapters:
        raise RuntimeError("No numbered chapters were found in the PDF outline")

    selected = next(
        (node for node in chapters if node.title.startswith(f"{SELECTED_CHAPTER_NUMBER}.")),
        chapters[0],
    )
    selected_index = chapters.index(selected)
    start_page = selected.page_index if selected.page_index is not None else 0
    if selected_index + 1 < len(chapters) and chapters[selected_index + 1].page_index is not None:
        end_page = chapters[selected_index + 1].page_index - 1
    else:
        end_page = len(pages) - 1

    section_nodes = selected.children
    page_texts: list[dict[str, Any]] = []
    raw_parts: list[str] = []
    for page_index in range(start_page, end_page + 1):
        text = pdf.page_text(pages[page_index])
        raw_parts.append(text)
        page_texts.append(
            {
                "page_index": page_index,
                "section": section_for_page(section_nodes, page_index) or selected.title,
                "text": text,
            }
        )

    cleaned_text = clean_chapter_text("\n".join(raw_parts))
    cleaned_page_texts = []
    for page in page_texts:
        cleaned_page_texts.append({**page, "text": clean_chapter_text(page["text"])})

    keywords_by_chapter = {node.title: chapter_keywords(node) for node in chapters}
    selected_keywords = keywords_by_chapter[selected.title]
    top_terms = top_terms_from_text(cleaned_text, selected_keywords)
    chunks = build_chunks(cleaned_page_texts, selected)
    stats = build_search_stats(chunks, selected_keywords)
    trainer_items = generate_trainer_items(chunks, selected_keywords)

    for chunk in chunks:
        chunk.pop("tokens", None)

    payload = {
        "source_pdf": PDF_PATH.name,
        "page_count": len(pages),
        "chapter_count": len(chapters),
        "selected_chapter": {
            "number": SELECTED_CHAPTER_NUMBER,
            "title": selected.title,
            "page_start": start_page + 1,
            "page_end": end_page + 1,
            "keywords": selected_keywords,
            "top_terms": top_terms,
            "cleaned_character_count": len(cleaned_text),
        },
        "chapters": [
            {
                "title": node.title,
                "page_start": node.page_index + 1 if node.page_index is not None else None,
                "keywords": keywords_by_chapter[node.title],
            }
            for node in chapters
        ],
        "outline": serialise_outline(outline),
        "search": stats,
        "chunks": chunks,
        "trainer": trainer_items,
        "notes": [
            "Индекс построен без LLM: ответы формируются извлечением предложений из выбранной главы.",
            "Картинки не попадают в текстовый слой PDF; подписи рисунков и эвристически найденные алгоритмические блоки исключаются.",
        ],
    }

    DATA_DIR.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Chapters: {len(chapters)}")
    print(f"Selected: {selected.title}, pages {start_page + 1}-{end_page + 1}")
    print(f"Chunks: {len(chunks)}")
    print(f"Trainer questions: {len(trainer_items)}")
    print(f"Wrote: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
