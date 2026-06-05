from __future__ import annotations

import json
import math
import os
import random
import re
from collections import Counter
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "index.json"
ONTOLOGY_PATH = ROOT / "data" / "ontology_chapter1.json"
PUBLIC_DIR = ROOT / "public"


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
    "во",
    "все",
    "для",
    "до",
    "если",
    "есть",
    "же",
    "за",
    "и",
    "из",
    "или",
    "к",
    "как",
    "ли",
    "между",
    "на",
    "не",
    "но",
    "о",
    "об",
    "от",
    "по",
    "под",
    "при",
    "с",
    "со",
    "так",
    "такая",
    "такие",
    "такое",
    "такой",
    "также",
    "то",
    "у",
    "что",
    "это",
}

WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯA-Z0-9])")
FORMULA_CHARS = set("{}[]=<>\x0e\x1a\x11\x02\x03∈∉⊂⊆⊄∩∪→←↔⇔¬&|^_")
GLUED_TERM_REPLACEMENTS = (
    (re.compile(r"\b(отношени(?:ями|ям|ях|ем|е|я|ю))(?=[а-яё])", re.IGNORECASE), r"\1 "),
    (re.compile(r"\b(функц(?:иями|иям|иях|ией|ия|ии|ию|ие|ий))(?=[а-яё])", re.IGNORECASE), r"\1 "),
    (re.compile(r"\b([а-яё]+(?:ами|ями|ью|остью|ю))или(?=[а-яё])", re.IGNORECASE), r"\1 или "),
)

SYMBOL_GLOSSARY = [
    {
        "aliases": {"kerf", "ker f", "ядро функции", "ядро функционального отношения"},
        "answer": (
            "ker f - это ядро функции f: отношение эквивалентности на области определения, "
            "которое объединяет элементы с одинаковым значением функции. В этой главе оно "
            "используется для построения фактормножества A / ker f и функции отождествления nat ker f."
        ),
        "section": "1.7. Отношения эквивалентности",
        "page_start": 240,
        "page_end": 241,
    },
    {
        "aliases": {"ker r", "kerr", "ядро отношения"},
        "answer": (
            "ker R - это ядро отношения R. Для отношения между множествами A и B ядро является "
            "отношением на A и связывает те элементы A, которые находятся в отношении R с одним "
            "и тем же элементом множества B."
        ),
        "section": "1.4. Отношения",
        "page_start": 180,
        "page_end": 181,
    },
]


def normalise_token(token: str) -> str:
    token = token.lower().replace("ё", "е")
    if token in {"пара", "пару", "парой", "паре", "пары", "парах", "парами"}:
        return "пар"
    if token.startswith("множеств"):
        return "множеств"
    if re.match(r"отношени(е|я|ю|ем|ям|ями|ях)", token):
        return "отношен"
    if re.match(r"функц(ия|ии|ию|ией|ие|ий|иям|иями|иях)", token):
        return "функц"
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
        "ым",
        "им",
        "ая",
        "ое",
        "ые",
        "ых",
        "ую",
        "юю",
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


def split_glued_terms(text: str) -> str:
    text = text.replace("\u043d\u0430\u0437\u044b\u0432\u0430\u044e\u0442 \u0441\u044f", "\u043d\u0430\u0437\u044b\u0432\u0430\u044e\u0442\u0441\u044f")
    text = text.replace("\u043d\u0430\u0431\u043e\u0440\u0430\u043c\u0438\u0438\u043b\u0438", "\u043d\u0430\u0431\u043e\u0440\u0430\u043c\u0438 \u0438\u043b\u0438")
    for pattern, replacement in GLUED_TERM_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"(?<=[A-Za-z])(?=[А-ЯЁа-яё])", " ", text)
    text = re.sub(r"(?<=[А-ЯЁа-яё])(?=[A-Za-z])", " ", text)
    text = re.sub(r"\bили(?=[а-яё])", "или ", text, flags=re.IGNORECASE)
    return text


def tokenize(text: str) -> list[str]:
    text = split_glued_terms(text)
    text = re.sub(r"([a-zа-яё]{3,})-([a-zа-яё]{3,})", r"\1\2", text, flags=re.IGNORECASE)
    result = []
    for word in WORD_RE.findall(text.lower().replace("ё", "е")):
        if len(word) < 2 or word.isdigit() or word in STOPWORDS:
            continue
        result.append(normalise_token(word))
    return result


def split_sentences(text: str) -> list[str]:
    text = re.sub(r"(\(\d+/\d+\))(?=[А-ЯЁ])", r"\1. ", text)
    text = re.sub(r"([.!?])(?=[А-ЯЁA-Z0-9])", r"\1 ", text)
    compact = re.sub(r"\s+", " ", text).strip()
    return [part.strip() for part in SENTENCE_SPLIT_RE.split(compact) if len(part.strip()) > 35]


def search_normalise(text: str) -> str:
    text = split_glued_terms(text)
    text = text.lower().replace("ё", "е")
    text = re.sub(r"\bker\s*([a-z])\b", r"ker \1", text)
    text = re.sub(r"([a-zа-яе]{3,})-([a-zа-яе]{3,})", r"\1\2", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    return text


def is_noise_sentence(sentence: str) -> bool:
    lowered = sentence.lower()
    if "название параграфа" in lowered or "ключевые термины" in lowered:
        return True
    if re.search(r"\b(while|select|return|end while|end for|then|else)\b|//", lowered):
        return True
    if any(ord(char) < 32 and char not in "\t\n\r" for char in sentence):
        return True
    if re.search(r"\b(def|big)\b|::|[a-zа-яё]\d|\d[a-zа-яё]", lowered, re.IGNORECASE):
        if re.search(r"[=<>∈∉⊂⊆∩∪&|{}[\]\x0e\x1a\x11\x02\x03]", sentence):
            return True
    formula_chars = sum(1 for char in sentence if char in FORMULA_CHARS)
    if formula_chars > 6:
        return True
    return formula_chars > 3 and len(sentence) < 260


def definition_clause(sentence: str) -> str:
    pattern = re.compile(
        r"[А-ЯЁ][а-яё-]+(?:\s+[а-яё-]+){1,14}\s+(?:называется|называются|называют|определяется)\s+[^.!?]{3,260}[.!?]",
        re.IGNORECASE,
    )
    matches = []
    for start in re.finditer(r"[А-ЯЁ]", sentence):
        match = pattern.match(sentence[start.start() :])
        if match:
            matches.append(match.group(0).strip())
    return min(matches, key=len) if matches else sentence


def display_text(text: str) -> str:
    text = split_glued_terms(text)
    text = "".join(char if ord(char) >= 32 or char in "\t\n\r" else " " for char in text)
    text = re.sub(r"\b\d+\s*/\s*\d+\b", " ", text)
    text = re.sub(r"\bker\s*([A-Za-z])\b", r"ker \1", text)
    text = re.sub(r"\bnat\s*ker\s*([A-Za-z])\b", r"nat ker \1", text)
    text = re.sub(r"\bA\s*=\s*ker\s*f\b", "A / ker f", text)
    text = re.sub(r"([А-ЯЁа-яё])-\s*([а-яё])", r"\1\2", text)
    text = re.sub(r"(?<=[а-яё])(?=[А-ЯЁ])", " ", text)
    text = re.sub(r"(?<=[A-Za-z0-9])(?=[А-ЯЁа-яё])", " ", text)
    text = re.sub(r"(?<=[А-ЯЁа-яё])(?=[A-Za-z0-9])", " ", text)
    text = re.sub(r"\b(его|ее|их)(?=элемент)", r"\1 ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bотношение\s+м(?=\s|$)", "отношением", text, flags=re.IGNORECASE)
    text = re.sub(r"\bотношение\s+м(?=между|порядка|эквивалентности)", "отношением ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bфункцие\s+й\b", "функцией", text, flags=re.IGNORECASE)
    text = re.sub(r"\bфункциям\s+и\b", "функциями", text, flags=re.IGNORECASE)
    text = re.sub(r"\bназывают\s+ся\b", "называются", text, flags=re.IGNORECASE)
    text = re.sub(r"\bсамоотношение\b", "само отношение", text, flags=re.IGNORECASE)
    text = re.sub(r"\bh\s*A;\s*B;\s*Ri\b", "<A, B, R>", text)
    text = re.sub(
        r"\b(называются|называется|называют|определяются|определяется)(?=[а-яё])",
        r"\1 ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\bтемсамым\b", "тем самым", text, flags=re.IGNORECASE)
    text = re.sub(r"\bэтовозможно\b", "это возможно", text, flags=re.IGNORECASE)
    text = re.sub(r"\bипрофессорами\b", "и профессорами", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=[А-ЯЁа-яёA-Za-z0-9])\((?=[А-ЯЁа-яёA-Za-z])", " (", text)
    text = re.sub(r"(?<=\))(?=[А-ЯЁа-яёA-Za-z])", " ", text)
    text = re.sub(r",(?=[А-ЯЁа-яёA-Za-z])", ", ", text)
    text = re.sub(r"([.!?])(?=[А-ЯЁA-Z0-9])", r"\1 ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = text.replace("\u043d\u0430\u0437\u044b\u0432\u0430\u044e\u0442 \u0441\u044f", "\u043d\u0430\u0437\u044b\u0432\u0430\u044e\u0442\u0441\u044f")
    text = text.replace("\u043d\u0430\u0431\u043e\u0440\u0430\u043c\u0438\u0438\u043b\u0438", "\u043d\u0430\u0431\u043e\u0440\u0430\u043c\u0438 \u0438\u043b\u0438")
    return text.strip()


DEFINITION_START_RE = re.compile(
    r"\b("
    r"Булевский\s+массив|"
    r"Бинарн(?:ое|ым)\s+отношен\w+|"
    r"Множество\b|"
    r"Функц\w+\b|"
    r"Отношение\b|"
    r"Рефлексивн\w+\b|"
    r"Антисимметричн\w+\b|"
    r"Антирефлексивн\w+\b|"
    r"Характеристическ\w+\s+функц\w+|"
    r"Семейство\b|"
    r"Последовательность\b|"
    r"Термин\b"
    r")",
    re.IGNORECASE,
)


def clean_definition_text(text: str) -> str:
    text = display_text(text)
    text = re.sub(r",?\s*причем для записи.*$", ".", text, flags=re.IGNORECASE)
    text = re.sub(r":\s*R\s+A\s+B\s*:", ".", text)
    text = re.sub(r"\.(?=[А-ЯЁA-Z])", ". ", text)
    for match in DEFINITION_START_RE.finditer(text):
        if match.start() == 0:
            return text.strip()
        prefix = text[: match.start()]
        if re.search(r"\d+\.\d+|\*|;|Название параграфа|Ключевые термины|алгоритм|оператор|операции", prefix, re.IGNORECASE):
            return text[match.start() :].strip()
    return text


CONTEXT_DEPENDENT_START_RE = re.compile(
    r"^(?:в\s+общем\s+случае\s+)?(?:"
    r"подобн\w+|"
    r"такие|такая|такой|таким\s+образом|"
    r"это\s+означает|"
    r"в\s+этом\s+случае|"
    r"при\s+этом|"
    r"они|она|оно|он"
    r")\b",
    re.IGNORECASE,
)


def needs_leading_context(sentence: str) -> bool:
    return bool(CONTEXT_DEPENDENT_START_RE.search(display_text(sentence).lower()))


def previous_context_sentence(sentences: list[str], index: int) -> str:
    for previous in reversed(sentences[:index]):
        text = clean_definition_text(previous)
        lowered = text.lower()
        if len(text) < 20:
            continue
        if any(marker in lowered for marker in ("название параграфа", "ключевые термины", "доказательство", "теорема")):
            continue
        if sum(1 for char in text if char in FORMULA_CHARS) > 18:
            continue
        return text
    return ""


def definition_text_with_context(sentences: list[str], index: int) -> str:
    text = clean_definition_text(sentences[index])
    if not needs_leading_context(text):
        return text
    previous = previous_context_sentence(sentences, index)
    if not previous:
        return text
    combined = f"{previous} {text}"
    if len(combined) > 700:
        return text
    return combined


def candidate_definition_text(sentence: str, target: str, *, target_before_connector: bool) -> str:
    if target_before_connector:
        sentence_display = display_text(sentence)
        target_display = display_text(target)
        index = sentence_display.lower().find(target_display.lower())
        if index >= 0:
            return sentence_display[index:]
    return definition_clause(sentence)


def dash_definition_clauses(text: str) -> list[str]:
    pattern = re.compile(
        r"[А-ЯЁ][а-яё-]+(?:\s+[а-яё-]+){0,4}\s+-\s+это\s+[^.!?]{8,260}[.!?]",
    )
    return [match.group(0).strip() for match in pattern.finditer(text)]


def symbol_glossary_answer(question: str) -> dict | None:
    normalised = search_normalise(question)
    compact = re.sub(r"\s+", "", normalised)
    for item in SYMBOL_GLOSSARY:
        for alias in item["aliases"]:
            alias_norm = search_normalise(alias)
            alias_compact = re.sub(r"\s+", "", alias_norm)
            if alias_norm in normalised or alias_compact in compact:
                return {
                    "answer": display_text(item["answer"]),
                    "citations": [
                        {
                            "section": item["section"],
                            "page_start": item["page_start"],
                            "page_end": item["page_end"],
                        }
                    ],
                    "matches": [
                        {
                            "section": item["section"],
                            "page_start": item["page_start"],
                            "page_end": item["page_end"],
                            "score": 12.0,
                            "preview": item["answer"],
                        }
                    ],
                }
    return None


DEFINITION_PROMPT_TOKENS = {
    normalise_token(word)
    for word in (
        "что",
        "такое",
        "такой",
        "такая",
        "объясни",
        "объясните",
        "определение",
        "дай",
        "дайте",
    )
}


DEFINITION_WORD_RE = re.compile(
    r"(называется|называются|называют|определяется|определяются)",
    re.IGNORECASE,
)
TARGET_BOUNDARY_RE = re.compile(r"[,.;:()]")
TARGET_WORD_BOUNDARIES = ("если", "то", "где", "что", "а", "причем", "поскольку")


def definition_query_tokens(question: str) -> set[str]:
    return set(tokenize(question)) - DEFINITION_PROMPT_TOKENS


def trim_definition_target(text: str, *, tail: bool) -> str:
    parts = [part.strip() for part in TARGET_BOUNDARY_RE.split(text) if part.strip()]
    if not parts:
        return ""
    target = parts[-1] if tail else parts[0]
    words = target.split()
    lowered = [word.lower().strip("«»\"'") for word in words]
    indexes = [
        index
        for index, word in enumerate(lowered)
        if word in TARGET_WORD_BOUNDARIES
    ]
    if indexes:
        index = indexes[-1] if tail else indexes[0]
        target = " ".join(words[index + 1 :] if tail else words[:index])
    return target.strip()


def definition_context_penalty(sentence: str, *, target_before_connector: bool) -> float:
    lowered = sentence.lower()
    penalty = 0.0
    if "определяемое следующим образом" in lowered or "определяемая следующим образом" in lowered:
        penalty += 18.0
    if sum(1 for char in sentence if char in FORMULA_CHARS) > 8:
        penalty += 12.0
    if "если" in lowered:
        penalty += 12.0
    if re.search(r"\b(обычно|также|тоже|иногда)\b", lowered):
        penalty += 32.0
    if re.search(r"\bможет\s+(?:быть|не)\b", lowered):
        penalty += 8.0
    if target_before_connector and re.search(r"\bназывается\s+[а-яё]+(?:ой|ым|им|ей|ою|ею)\b", lowered):
        penalty += 6.0
    return penalty


def definition_target_penalty(target: str) -> float:
    target = split_glued_terms(target)
    lowered = target.lower()
    penalty = 0.0
    if re.search(r"\d|Def|[{}=<>]", target):
        penalty += 12.0
    if len(re.findall(r"\b[A-ZА-ЯЁ]\b", target)) >= 3:
        penalty += 24.0
    if re.search(r"\bn\b|аргумент|n-мест", lowered):
        penalty += 24.0
    if re.search(r"\b(график|область|значени|отправлен|прибыт)", lowered):
        penalty += 100.0
    if re.search(r"\b[A-Za-z]\b", target) and not any(f" {preposition} " in lowered for preposition in ("из", "в", "между", "на")):
        penalty += 30.0
    if (
        re.search(r"\b[A-ZА-ЯЁ]\s*$", target)
        and not any(f" {preposition} " in lowered for preposition in ("из", "в", "между", "на"))
    ):
        penalty += 10.0
    return penalty


def enumerated_definition_clauses(sentence: str) -> list[str]:
    if ":" not in sentence:
        return []
    prefix, rest = sentence.split(":", 1)
    if not DEFINITION_WORD_RE.search(prefix):
        return []
    clean_prefix = display_text(prefix)
    context_match = re.search(r"(?:тотальная\s+)?функция\s+[A-Za-zА-Яа-я]?", clean_prefix, re.IGNORECASE)
    context = display_text(context_match.group(0)) if context_match else clean_prefix
    if context:
        context = context[0].upper() + context[1:]
    clauses = []
    for part in re.split(r";", rest):
        part = part.strip()
        if not part:
            continue
        if "если" not in part.lower():
            continue
        clauses.append(f"{context} называется {part}" if context else part)
    return clauses


TRAINER_CONCEPTS = [
    {"label": "Множество", "query": "множество"},
    {"label": "Бинарное отношение", "query": "отношение"},
    {"label": "Функция", "query": "функция"},
    {"label": "Отношение эквивалентности", "query": "отношение эквивалентности"},
    {"label": "Отношение порядка", "query": "отношение порядка"},
    {"label": "Инъекция", "query": "инъекция"},
    {"label": "Сюръекция", "query": "сюръекция"},
    {"label": "Биекция", "query": "биекция"},
    {"label": "Характеристическая функция", "query": "характеристическая функция"},
]

ONTOLOGY_TERM_BLOCKLIST = (
    "алгоритм",
    "перенумер",
    "добавить",
    "удалить",
    "итератор",
    "тело цикла",
    "and",
    "or",
    "xor",
    "not",
    "обязательный",
    "оператор",
    "операции над",
    "переход к",
    "перечисление",
    "построение",
    "выделение",
    "порождающая процедура",
)

TRAINER_TERM_BLOCKLIST = (
    "элемент",
    "объект",
    "буква",
    "слово",
    "алфавит",
    "язык",
    "последовательность",
    "упорядоченность",
    "парадокс",
    "односвязный список",
    "упорядоченный список",
    "семейство",
    "сокращение",
    "редукция",
    "уравнение гомоморфизма",
    "индукция",
    "фасетный",
    "иерархический",
)

TRAINER_TERM_ALLOWLIST = {
    "множество",
    "пустое множество",
    "подмножество",
    "покрытие",
    "битовая шкала",
    "упорядоченная пара",
    "бинарное отношение",
    "отношение",
    "бинарное отношение на множестве",
    "отношение эквивалентности",
    "отношение порядка",
    "частичный порядок",
    "линейный порядок",
    "функциональное отношение",
    "функция",
    "инъекция",
    "сюръекция",
    "изоморфизм",
    "кортеж",
    "характеристическая функция",
    "характеристическая функция мультимножества",
    "предикат",
    "классификатор",
    "рефлексивное отношение",
    "антирефлексивное отношение",
    "симметричное отношение",
    "антисимметричное отношение",
    "транзитивное отношение",
    "транзитивное сокращение",
    "диаграмма хассе",
    "неподвижная точка",
    "вполне упорядоченное множество",
}


def is_definition_ontology_term(term: object) -> bool:
    text = display_text(str(term)).lower().replace("ё", "е").strip()
    if not text or len(text) > 60:
        return False
    return not any(blocked in text for blocked in ONTOLOGY_TERM_BLOCKLIST)


def is_trainer_term(term: object) -> bool:
    text = display_text(str(term)).lower().replace("ё", "е").strip()
    if not is_definition_ontology_term(text):
        return False
    if text not in TRAINER_TERM_ALLOWLIST:
        return False
    return text not in TRAINER_TERM_BLOCKLIST


def is_good_trainer_answer(term: str, answer: str) -> bool:
    clean_answer = display_text(answer)
    lowered = clean_answer.lower().replace("ё", "е")
    if len(clean_answer) < 35:
        return False
    if any(marker in lowered for marker in ("название параграфа", "ключевые термины", "алгоритм", "пример.", "доказательство")):
        return False
    if any(marker in lowered for marker in ("связано", "связаны", "выделяются")) and not any(
        marker in lowered for marker in ("называ", "это", "если", "является")
    ):
        return False
    term_tokens = set(tokenize(term))
    answer_tokens = set(tokenize(clean_answer))
    if not term_tokens or len(term_tokens & answer_tokens) < min(2, len(term_tokens)):
        return False
    return True


DEFINITION_INDEX_MARKERS = (
    "называется",
    "называются",
    "называют",
    "определяется",
    "определяют",
    "говорят",
    "обозначим",
    "обозначается",
    "это",
    "является",
)

DEFINITION_INDEX_BAD_MARKERS = (
    "название параграфа",
    "ключевые термины",
    "теорема",
    "доказательство",
    "следствие",
    "пример",
    "замечание",
    "алгоритм",
    "вход:",
    "выход:",
    "for ",
    "while ",
    "end for",
)


def definition_query_phrase(question: str) -> str:
    words = [
        word
        for word in WORD_RE.findall(question.lower().replace("ё", "е"))
        if word not in STOPWORDS and word not in DEFINITION_PROMPT_TOKENS
    ]
    return search_normalise(" ".join(words))


def is_definition_index_candidate(sentence: str) -> bool:
    text = display_text(sentence)
    lowered = search_normalise(text)
    if len(text) < 35 or len(text) > 700:
        return False
    if any(marker in lowered for marker in DEFINITION_INDEX_BAD_MARKERS):
        return False
    if any(marker in lowered for marker in DEFINITION_INDEX_MARKERS):
        return True
    return False


def load_ontology_entries() -> list[dict]:
    if not ONTOLOGY_PATH.exists():
        return []
    payload = json.loads(ONTOLOGY_PATH.read_text(encoding="utf-8"))
    return list(payload.get("entries", []))


class RagEngine:
    def __init__(self, data: dict):
        self.data = data
        self.chunks = [dict(chunk) for chunk in data["chunks"]]
        self.ontology_entries = load_ontology_entries()
        self.ontology_term_sections: dict[frozenset[str], set[str]] = {}
        for entry in self.ontology_entries:
            section = str(entry.get("section", ""))
            for term in entry.get("terms", []):
                if not is_definition_ontology_term(term):
                    continue
                tokens = frozenset(tokenize(str(term)))
                if tokens:
                    self.ontology_term_sections.setdefault(tokens, set()).add(section)
        self.add_ontology_chunks()
        self.stats = data["search"]
        self.trainer: dict[str, dict] = {}
        self.section_start_pages: dict[str, int] = {}
        for chunk in self.chunks:
            section = chunk["section"]
            page = int(chunk["page_start"])
            self.section_start_pages[section] = min(page, self.section_start_pages.get(section, page))
        self.rebuild_search_stats()
        self.definition_index = self.build_definition_index()
        self.trainer = {item["id"]: item for item in self.build_trainer_items()}

    def add_ontology_chunks(self) -> None:
        for index, entry in enumerate(self.ontology_entries):
            text = entry.get("text", "")
            ontology_text = " ".join([entry.get("title", ""), text]).strip()
            if not ontology_text:
                continue
            self.chunks.append(
                {
                    "id": f"ontology-{index}",
                    "text": ontology_text,
                    "page_start": int(entry["page"]),
                    "page_end": int(entry["page"]),
                    "section": entry["section"],
                    "source": "ontology",
                }
            )

    def rebuild_search_stats(self) -> None:
        doc_freq: Counter[str] = Counter()
        lengths = []
        for chunk in self.chunks:
            tokens = tokenize(chunk["text"])
            counts = Counter(tokens)
            chunk["term_counts"] = dict(counts)
            chunk["keyword_hits"] = sorted(counts)
            doc_freq.update(set(tokens))
            lengths.append(len(tokens))
        self.df = dict(doc_freq)
        self.avg_len = max(sum(lengths) / max(1, len(lengths)), 1.0)
        self.n_docs = max(len(self.chunks), 1)

    def build_definition_index(self) -> list[dict]:
        entries: list[dict] = []
        seen: set[tuple[str, str, int]] = set()
        for chunk in self.chunks:
            sentences = split_sentences(chunk["text"])
            for index, sentence in enumerate(sentences):
                text = definition_text_with_context(sentences, index)
                if not is_definition_index_candidate(text):
                    continue
                key = (chunk["section"], text[:160], int(chunk["page_start"]))
                if key in seen:
                    continue
                seen.add(key)
                norm = search_normalise(text)
                entries.append(
                    {
                        "text": text,
                        "norm": norm,
                        "tokens": set(tokenize(text)),
                        "section": chunk["section"],
                        "page_start": int(chunk["page_start"]),
                        "page_end": int(chunk["page_end"]),
                        "source": chunk.get("source", "text"),
                    }
                )
        return entries

    def build_trainer_items(self) -> list[dict]:
        items = []
        concepts = [
            concept
            for concept in TRAINER_CONCEPTS
            if is_trainer_term(concept["label"]) or is_trainer_term(concept["query"])
        ]
        seen = {concept["query"].lower() for concept in concepts}
        seen_labels = {display_text(concept["label"]).lower() for concept in concepts}
        ontology_concept_count = 0
        for entry in self.ontology_entries:
            for term in entry.get("terms", []):
                if ontology_concept_count >= 35:
                    break
                if not is_trainer_term(term):
                    continue
                key = str(term).lower()
                label_key = display_text(str(term)).lower()
                if key in seen or label_key in seen_labels or len(str(term)) > 48:
                    continue
                seen.add(key)
                seen_labels.add(label_key)
                concepts.append({"label": str(term), "query": str(term)})
                ontology_concept_count += 1
            if ontology_concept_count >= 35:
                break

        for concept in concepts:
            result = self.answer(f"что такое {concept['query']}")
            answer = str(result.get("answer", "")).strip()
            citations = result.get("citations") or []
            if not answer or not citations or not is_good_trainer_answer(str(concept["query"]), answer):
                continue
            citation = citations[0]
            expected_tokens = sorted(set(tokenize(f"{concept['query']} {answer}")))
            items.append(
                {
                    "id": f"concept-{len(items)}",
                    "question": f"Что такое «{concept['label']}»?",
                    "keyword": concept["query"],
                    "answer_sentence": answer,
                    "expected_tokens": expected_tokens,
                    "section": citation["section"],
                    "page": citation["page_start"],
                }
            )
        return items

    def definition_candidate_score(self, query_tokens: set[str], target: str, chunk: dict) -> float | None:
        target_tokens = set(tokenize(target)) - DEFINITION_PROMPT_TOKENS
        if not query_tokens or not target_tokens or not query_tokens.issubset(target_tokens):
            return None

        extra_terms = len(target_tokens - query_tokens)
        score = 24.0 - 4.0 * extra_terms
        score -= definition_target_penalty(target)
        if target_tokens == query_tokens:
            score += 8.0
        target_lower = target.lower()
        if "между множествами" in target_lower or (
            query_tokens != {"отношен"} and re.search(r"\bиз\s+[A-ZА-ЯЁ]\s+в\s+[A-ZА-ЯЁ]\b", target)
        ):
            score += 18.0

        start_page = self.section_start_pages.get(chunk["section"], int(chunk["page_start"]))
        distance = max(0, int(chunk["page_start"]) - start_page)
        score -= min(8.0, 0.35 * distance)

        section_tokens = set(tokenize(chunk["section"]))
        if query_tokens.issubset(section_tokens):
            section_extra_terms = len(section_tokens - query_tokens)
            if section_tokens == query_tokens:
                score += 10.0
            else:
                score += max(0.0, 4.0 - 2.0 * section_extra_terms)
            score += max(0.0, 4.0 - distance)

        return score

    def ontology_sections_for_query(self, query_tokens: set[str]) -> set[str]:
        sections: set[str] = set()
        for term_tokens, term_sections in self.ontology_term_sections.items():
            if not query_tokens or not term_tokens:
                continue
            if query_tokens == set(term_tokens) or query_tokens.issubset(term_tokens):
                sections.update(term_sections)
                continue
            if len(term_tokens) <= 1:
                continue
            if term_tokens.issubset(query_tokens):
                sections.update(term_sections)
                continue
            overlap = len(query_tokens & set(term_tokens))
            if overlap >= min(len(query_tokens), len(term_tokens)) and overlap >= 2:
                sections.update(term_sections)
        return sections

    def definition_index_answer(self, question: str, preferred_sections: set[str] | None = None) -> dict | None:
        query_tokens = definition_query_tokens(question)
        if not query_tokens:
            return None

        phrase = definition_query_phrase(question)
        candidates: list[tuple[float, dict]] = []
        for entry in self.definition_index:
            if preferred_sections and entry["section"] not in preferred_sections:
                continue

            tokens = entry["tokens"]
            overlap = len(query_tokens & tokens)
            if overlap < min(len(query_tokens), 2):
                continue

            lowered = entry["norm"]
            score = 3.5 * overlap
            if query_tokens.issubset(tokens):
                score += 7.0
            if phrase and phrase in lowered:
                score += 14.0
            if preferred_sections and entry["section"] in preferred_sections:
                score += 5.0
            if entry.get("source") == "ontology":
                score += 2.0

            if any(marker in lowered for marker in ("называется", "называют", "называются", "определяется", "обозначим", "обозначается")):
                score += 5.0
                called_match = re.search(r"\b(называется|называют|называются|определяется|определяют|обозначим|обозначается)\b", lowered)
                if called_match:
                    before_tokens = set(tokenize(entry["text"][: called_match.start()]))
                    after_text = entry["text"][called_match.end() :]
                    after_tokens = set(tokenize(after_text))
                    if query_tokens & after_tokens:
                        score += 9.0
                    if query_tokens & before_tokens:
                        score += 4.0
                    after_target_tokens = set(tokenize(trim_definition_target(after_text, tail=False)))
                    if len(before_tokens) >= 2 and after_target_tokens == query_tokens:
                        score += 12.0
                    elif len(before_tokens) >= 2 and query_tokens.issubset(after_target_tokens):
                        score += max(0.0, 7.0 - 2.0 * len(after_target_tokens - query_tokens))
            else:
                score -= 8.0
            if "если" in lowered:
                score += 2.0
            if "при этом говорят" in lowered:
                score += 5.0
            if "это" in lowered:
                score += 2.0

            if "связано" in lowered or "связаны" in lowered or "выделяются" in lowered:
                score -= 7.0
            if "может быть" in lowered or "может не обладать" in lowered:
                score -= 8.0
            if "представляется" in lowered or "представление" in lowered:
                score -= 8.0
            if any(marker in lowered for marker in DEFINITION_INDEX_BAD_MARKERS):
                score -= 20.0
            if sum(1 for char in entry["text"] if char in FORMULA_CHARS) > 12:
                score -= 3.0

            start_page = self.section_start_pages.get(entry["section"], entry["page_start"])
            score -= min(5.0, 0.12 * max(0, entry["page_start"] - start_page))
            candidates.append((score, entry))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        score, entry = candidates[0]
        if score < 10.0:
            return None
        return {
            "answer": entry["text"],
            "citations": [
                {
                    "section": entry["section"],
                    "page_start": entry["page_start"],
                    "page_end": entry["page_end"],
                }
            ],
            "matches": [
                {
                    "section": entry["section"],
                    "page_start": entry["page_start"],
                    "page_end": entry["page_end"],
                    "score": round(score, 3),
                    "preview": entry["text"][:260],
                }
            ],
        }

    def generic_definition_answer(self, question: str, preferred_sections: set[str] | None = None) -> dict | None:
        query_tokens = definition_query_tokens(question)
        if not query_tokens:
            return None

        candidates: list[tuple[float, str, dict]] = []
        for chunk in self.chunks:
            if preferred_sections and chunk["section"] not in preferred_sections:
                continue
            if preferred_sections and chunk.get("source") == "ontology":
                continue
            for clause in dash_definition_clauses(chunk["text"]):
                subject = clause.split(" - ", 1)[0]
                score = self.definition_candidate_score(query_tokens, subject, chunk)
                if score is not None:
                    penalty = definition_context_penalty(clause, target_before_connector=True)
                    candidates.append((score + 1.0 - penalty, clause, chunk))

            for sentence in split_sentences(chunk["text"]):
                if is_noise_sentence(sentence) and not DEFINITION_WORD_RE.search(sentence):
                    continue
                for clause in enumerated_definition_clauses(sentence):
                    target = clause.split("если", 1)[0]
                    score = self.definition_candidate_score(query_tokens, target, chunk)
                    if score is not None:
                        penalty = definition_context_penalty(clause, target_before_connector=False)
                        candidates.append((score + 2.0 - penalty, clause, chunk))

                for match in DEFINITION_WORD_RE.finditer(sentence):
                    before = sentence[: match.start()]
                    after = sentence[match.end() :]

                    before_target = trim_definition_target(before, tail=True)
                    score = self.definition_candidate_score(query_tokens, before_target, chunk)
                    if score is not None:
                        penalty = definition_context_penalty(sentence, target_before_connector=True)
                        candidates.append(
                            (
                                score - penalty,
                                candidate_definition_text(sentence, before_target, target_before_connector=True),
                                chunk,
                            )
                        )

                    after_target = trim_definition_target(after, tail=False)
                    score = self.definition_candidate_score(query_tokens, after_target, chunk)
                    if score is not None:
                        penalty = definition_context_penalty(sentence, target_before_connector=False)
                        candidates.append(
                            (
                                score + 2.0 - penalty,
                                candidate_definition_text(sentence, after_target, target_before_connector=False),
                                chunk,
                            )
                        )

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[0], reverse=True)
        score, sentence, chunk = candidates[0]
        return {
            "answer": clean_definition_text(sentence),
            "citations": [
                {
                    "section": chunk["section"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                }
            ],
            "matches": [
                {
                    "section": chunk["section"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "score": round(score, 3),
                    "preview": chunk["text"][:260],
                }
            ],
        }

    def section_definition_answer(self, question: str, preferred_sections: set[str]) -> dict | None:
        query_tokens = definition_query_tokens(question)
        if not query_tokens or not preferred_sections:
            return None

        query_phrase = search_normalise(" ".join(
            word
            for word in WORD_RE.findall(question.lower().replace("ё", "е"))
            if word not in STOPWORDS and word not in DEFINITION_PROMPT_TOKENS
        ))
        candidates: list[tuple[float, str, dict]] = []
        for chunk in self.chunks:
            if chunk["section"] not in preferred_sections:
                continue
            start_page = self.section_start_pages.get(chunk["section"], int(chunk["page_start"]))
            distance = max(0, int(chunk["page_start"]) - start_page)
            for sentence in split_sentences(chunk["text"]):
                tokens = set(tokenize(sentence))
                overlap = len(query_tokens & tokens)
                if overlap < min(len(query_tokens), 2):
                    continue
                sentence_norm = search_normalise(sentence)
                sentence_is_noise = is_noise_sentence(sentence)
                if (
                    sentence_is_noise
                    and chunk.get("source") != "ontology"
                    and not (query_phrase and query_phrase in sentence_norm)
                    and "при этом говорят" not in sentence_norm.lower()
                ):
                    continue
                if (
                    sentence_is_noise
                    and not DEFINITION_WORD_RE.search(sentence)
                    and "говорят" not in sentence_norm.lower()
                    and not (query_phrase and query_phrase in sentence_norm)
                ):
                    continue
                score = 4.0 * overlap
                if query_tokens.issubset(tokens):
                    score += 5.0
                if query_phrase and query_phrase in sentence_norm:
                    score += 5.0
                if chunk.get("source") == "ontology":
                    score += 8.0
                elif sentence_is_noise:
                    score -= 5.0
                lowered = sentence_norm.lower()
                if "говорят" in lowered or "называется" in lowered or "называют" in lowered:
                    score += 3.5
                if "при этом говорят" in lowered:
                    score += 6.0
                if "если" in lowered:
                    score += 1.5
                called_match = re.search(r"\b(называется|называют|называются|определяется)\b", lowered)
                if called_match:
                    before_tokens = set(tokenize(sentence[: called_match.start()]))
                    after_tokens = set(tokenize(sentence[called_match.end() :]))
                    if query_tokens & after_tokens:
                        score += 3.0
                    if query_tokens and not (query_tokens & before_tokens) and not (query_tokens & after_tokens):
                        score -= 8.0
                if "собственн" in lowered and len(query_tokens) == 1 and "собственн" not in query_tokens:
                    score -= 2.0
                score -= min(7.0, 0.25 * distance)
                candidates.append((score, sentence, chunk))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        score, sentence, chunk = candidates[0]
        return {
            "answer": clean_definition_text(sentence),
            "citations": [
                {
                    "section": chunk["section"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                }
            ],
            "matches": [
                {
                    "section": chunk["section"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "score": round(score, 3),
                    "preview": chunk["text"][:260],
                }
            ],
        }

    def score_chunk(self, query_tokens: list[str], raw_query: str, chunk: dict) -> float:
        term_counts = chunk.get("term_counts", {})
        chunk_len = max(sum(term_counts.values()), 1)
        score = 0.0
        k1 = 1.45
        b = 0.72
        for term in query_tokens:
            tf = term_counts.get(term, 0)
            if not tf:
                continue
            df = self.df.get(term, 0)
            idf = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))
            denom = tf + k1 * (1 - b + b * chunk_len / self.avg_len)
            score += idf * (tf * (k1 + 1)) / denom
            if term in chunk.get("keyword_hits", []):
                score += 0.35
        query_norm = raw_query.lower().replace("ё", "е").strip()
        if len(query_norm) > 4 and query_norm in chunk["text"].lower().replace("ё", "е"):
            score += 2.0
        return score

    def answer(self, question: str) -> dict:
        glossary = symbol_glossary_answer(question)
        if glossary is not None:
            return glossary

        query_tokens = tokenize(question)
        if not query_tokens:
            return {
                "answer": "Задайте вопрос по терминам выбранной главы.",
                "citations": [],
                "matches": [],
            }

        scored = [
            (self.score_chunk(query_tokens, question, chunk), chunk)
            for chunk in self.chunks
        ]
        scored = [(score, chunk) for score, chunk in scored if score > 0]
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            return {
                "answer": "В выбранной главе не нашлось достаточно близкого фрагмента.",
                "citations": [],
                "matches": [],
            }

        sentence_scores: list[tuple[float, str, dict]] = []
        query_set = set(query_tokens)
        wants_definition = bool(re.search(r"\b(что такое|что называется|определени|объясни|объясните)\b", question.lower()))
        raw_words = [
            word
            for word in WORD_RE.findall(question.lower().replace("ё", "е"))
            if word not in STOPWORDS and word not in {"что", "такое", "такой", "какой", "какая", "объясни", "объясните"}
        ]
        query_phrase = search_normalise(" ".join(raw_words))
        term_like_query = len(query_set) <= 4 and not re.search(r"\b(почему|зачем|когда|где|сколько|приведи|докажи)\b", question.lower())
        definition_mode = wants_definition or term_like_query
        if definition_mode:
            definition_tokens = definition_query_tokens(question)
            preferred_sections = self.ontology_sections_for_query(definition_tokens)
            use_section_first = bool(preferred_sections) and (len(definition_tokens) > 1 or len(preferred_sections) == 1)
            if use_section_first:
                indexed_definition = self.definition_index_answer(question, preferred_sections)
                if indexed_definition is not None:
                    return indexed_definition
                section_definition = self.section_definition_answer(question, preferred_sections)
                if section_definition is not None:
                    return section_definition
            else:
                generic_definition = self.generic_definition_answer(question)
                if generic_definition is not None:
                    return generic_definition
                indexed_definition = self.definition_index_answer(question)
                if indexed_definition is not None:
                    return indexed_definition
                section_definition = self.section_definition_answer(question, preferred_sections)
                if section_definition is not None:
                    return section_definition
            indexed_definition = self.definition_index_answer(question, preferred_sections)
            if indexed_definition is not None:
                return indexed_definition
            generic_definition = self.generic_definition_answer(question, preferred_sections)
            if generic_definition is not None:
                return generic_definition
            indexed_definition = self.definition_index_answer(question)
            if indexed_definition is not None:
                return indexed_definition
            generic_definition = self.generic_definition_answer(question)
            if generic_definition is not None:
                return generic_definition

        selected_chapter = self.data.get("selected_chapter", {})
        title_tokens = set(tokenize(selected_chapter.get("title", "")))
        if query_set and len(query_set) >= 2 and query_set.issubset(title_tokens):
            intro_chunk = next(
                (
                    chunk
                    for chunk in self.chunks
                    if chunk["page_start"] == selected_chapter.get("page_start")
                    and chunk["section"] == selected_chapter.get("title")
                ),
                None,
            )
            if intro_chunk:
                intro_sentences = [
                    display_text(sentence)
                    for sentence in split_sentences(intro_chunk["text"])
                    if not is_noise_sentence(sentence)
                ]
                intro_answer = " ".join(intro_sentences[:2])
                if intro_answer:
                    return {
                        "answer": intro_answer,
                        "citations": [
                            {
                                "section": intro_chunk["section"],
                                "page_start": intro_chunk["page_start"],
                                "page_end": intro_chunk["page_end"],
                            }
                        ],
                        "matches": [
                            {
                                "section": intro_chunk["section"],
                                "page_start": intro_chunk["page_start"],
                                "page_end": intro_chunk["page_end"],
                                "score": 10.0,
                                "preview": intro_chunk["text"][:260],
                            }
                        ],
                    }

        if definition_mode and query_set:
            dash_definitions: list[tuple[float, str, dict]] = []
            for chunk in self.chunks:
                for clause in dash_definition_clauses(chunk["text"]):
                    subject = clause.split(" - ", 1)[0]
                    subject_tokens = set(tokenize(subject))
                    overlap = len(query_set & subject_tokens)
                    if not subject_tokens or overlap < min(2, len(query_set)):
                        continue
                    dash_definitions.append((7.0 + overlap, clause, chunk))
            if dash_definitions:
                dash_definitions.sort(key=lambda item: item[0], reverse=True)
                _, sentence, chunk = dash_definitions[0]
                return {
                    "answer": clean_definition_text(sentence),
                    "citations": [
                        {
                            "section": chunk["section"],
                            "page_start": chunk["page_start"],
                            "page_end": chunk["page_end"],
                        }
                    ],
                    "matches": [
                        {
                            "section": chunk["section"],
                            "page_start": chunk["page_start"],
                            "page_end": chunk["page_end"],
                            "score": round(dash_definitions[0][0], 3),
                            "preview": chunk["text"][:260],
                        }
                    ],
                }
            direct_definitions: list[tuple[float, str, dict]] = []
            for chunk in self.chunks:
                for sentence in split_sentences(chunk["text"]):
                    called_match = re.search(r"\b(называется|называются|называют|определяется)\b", sentence.lower())
                    if not called_match:
                        continue
                    after_tokens = set(tokenize(sentence[called_match.end() :]))
                    if len(query_set & after_tokens) < min(2, len(query_set)):
                        continue
                    clause = definition_clause(sentence)
                    clause_tokens = set(tokenize(clause))
                    score = 5.0 + len(query_set & clause_tokens)
                    direct_definitions.append((score, clause, chunk))
            if direct_definitions:
                direct_definitions.sort(key=lambda item: item[0], reverse=True)
                _, sentence, chunk = direct_definitions[0]
                return {
                    "answer": clean_definition_text(sentence),
                    "citations": [
                        {
                            "section": chunk["section"],
                            "page_start": chunk["page_start"],
                            "page_end": chunk["page_end"],
                        }
                    ],
                    "matches": [
                        {
                            "section": chunk["section"],
                            "page_start": chunk["page_start"],
                            "page_end": chunk["page_end"],
                            "score": round(direct_definitions[0][0], 3),
                            "preview": chunk["text"][:260],
                        }
                    ],
                }
        for chunk_score, chunk in scored[:5]:
            for sentence in split_sentences(chunk["text"]):
                if is_noise_sentence(sentence):
                    continue
                tokens = set(tokenize(sentence))
                overlap = len(tokens & query_set)
                if not overlap:
                    continue
                sentence_score = overlap + 0.08 * chunk_score
                sentence_norm = search_normalise(sentence)
                if query_phrase and len(query_phrase) > 5 and query_phrase in sentence_norm:
                    sentence_score += 1.4 if definition_mode else 2.8
                if len(query_set) > 1 and query_set.issubset(tokens):
                    sentence_score += 1.2
                called_match = re.search(r"\b(называется|называют|определяется)\b", sentence.lower())
                if definition_mode and re.search(
                    r"\b(называется|называют|определяется|означает|есть|является)\b",
                    sentence.lower(),
                ):
                    sentence_score += 1.5
                    if overlap >= min(2, len(query_set)):
                        sentence_score += 3.0
                    if called_match:
                        after_called = sentence[called_match.end() :]
                        after_tokens = set(tokenize(after_called))
                        if query_set and len(query_set & after_tokens) >= min(2, len(query_set)):
                            sentence_score += 4.0
                        elif query_set and len(query_set & after_tokens) < min(2, len(query_set)):
                            sentence_score -= 3.0
                sentence_scores.append((sentence_score, sentence, chunk))
        sentence_scores.sort(key=lambda item: item[0], reverse=True)

        selected: list[tuple[str, dict]] = []
        seen = set()
        if definition_mode:
            ontology_sentences = [
                (sentence, chunk)
                for _, sentence, chunk in sentence_scores
                if chunk.get("source") == "ontology"
            ]
            if ontology_sentences:
                first_chunk = ontology_sentences[0][1]
                for sentence, chunk in ontology_sentences:
                    if chunk is not first_chunk:
                        continue
                    key = sentence[:120]
                    if key in seen:
                        continue
                    seen.add(key)
                    selected.append((sentence, chunk))
                    if len(selected) >= 2:
                        break
        for _, sentence, chunk in sentence_scores:
            if selected:
                break
            key = sentence[:120]
            if key in seen:
                continue
            seen.add(key)
            selected.append((sentence, chunk))
            if len(selected) >= 3:
                break

        if not selected:
            _, best_chunk = scored[0]
            selected = [(split_sentences(best_chunk["text"])[0], best_chunk)]

        answer_text = display_text(" ".join(sentence for sentence, _ in selected))
        citations = []
        citation_keys = set()
        for _, chunk in selected:
            key = (chunk["section"], chunk["page_start"], chunk["page_end"])
            if key in citation_keys:
                continue
            citation_keys.add(key)
            citations.append(
                {
                    "section": chunk["section"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                }
            )

        return {
            "answer": answer_text,
            "citations": citations,
            "matches": [
                {
                    "section": chunk["section"],
                    "page_start": chunk["page_start"],
                    "page_end": chunk["page_end"],
                    "score": round(score, 3),
                    "preview": chunk["text"][:260],
                }
                for score, chunk in scored[:3]
            ],
        }

    def trainer_question(self) -> dict:
        items = list(self.trainer.values())
        if not items:
            return {}
        item = random.choice(items)
        return {
            "id": item["id"],
            "question": item["question"],
            "keyword": item["keyword"],
            "section": item["section"],
            "page": item["page"],
        }

    def trainer_test(self, count: int = 5) -> dict:
        items = list(self.trainer.values())
        random.shuffle(items)
        selected = items[: max(1, min(count, len(items)))]
        return {
            "questions": [
                {
                    "id": item["id"],
                    "question": item["question"],
                    "keyword": item["keyword"],
                    "section": item["section"],
                    "page": item["page"],
                }
                for item in selected
            ]
        }

    def check_trainer_answer(self, question_id: str, answer: str) -> dict:
        item = self.trainer.get(question_id)
        if item is None:
            return {"correct": False, "score": 0.0, "explanation": "Вопрос не найден."}

        answer_tokens = set(tokenize(answer))
        expected_tokens = set(item.get("expected_tokens", []))
        keyword_tokens = set(tokenize(item.get("keyword", "")))
        if not answer_tokens:
            ratio = 0.0
        else:
            denominator = max(4, min(12, len(expected_tokens)))
            ratio = len(answer_tokens & expected_tokens) / denominator
        keyword_bonus = 0.15 if answer_tokens & keyword_tokens else 0.0
        score = min(1.0, ratio + keyword_bonus)
        correct = score >= 0.38 and len(answer_tokens & expected_tokens) >= 2
        explanation = item["answer_sentence"]
        if re.search(r"\b(называется|называются|называют|определяется)\b", explanation.lower()):
            explanation = definition_clause(explanation)
        explanation = clean_definition_text(explanation)
        return {
            "correct": correct,
            "score": round(score, 2),
            "explanation": explanation,
            "section": item["section"],
            "page": item["page"],
        }


def load_engine() -> RagEngine:
    if not DATA_PATH.exists():
        raise RuntimeError("data/index.json was not found. Run scripts/build_data.py first.")
    return RagEngine(json.loads(DATA_PATH.read_text(encoding="utf-8")))


ENGINE = load_engine()


class Handler(BaseHTTPRequestHandler):
    server_version = "DMRag/1.0"

    def log_message(self, format: str, *args) -> None:
        print("%s - %s" % (self.address_string(), format % args))

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/meta":
            data = ENGINE.data
            selected_chapter = dict(data["selected_chapter"])
            ontology_terms = []
            seen_terms = {term.lower() for term in selected_chapter.get("keywords", [])}
            for entry in ENGINE.ontology_entries:
                for term in entry.get("terms", []):
                    if not is_definition_ontology_term(term):
                        continue
                    key = str(term).lower()
                    if key in seen_terms:
                        continue
                    seen_terms.add(key)
                    ontology_terms.append(str(term))
            selected_chapter["ontology_terms"] = ontology_terms
            selected_chapter["keywords"] = selected_chapter.get("keywords", []) + ontology_terms
            self.send_json(
                {
                    "source_pdf": data["source_pdf"],
                    "page_count": data["page_count"],
                    "chapter_count": data["chapter_count"],
                    "selected_chapter": selected_chapter,
                    "chapters": data["chapters"],
                    "notes": data["notes"],
                }
            )
            return
        if parsed.path == "/api/trainer/question":
            self.send_json(ENGINE.trainer_question())
            return
        if parsed.path == "/api/trainer/test":
            params = parse_qs(parsed.query)
            count = int(params.get("count", ["5"])[0])
            self.send_json(ENGINE.trainer_test(count))
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            self.send_json({"error": "Некорректный JSON."}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/ask":
            question = str(payload.get("question", ""))[:1000]
            self.send_json(ENGINE.answer(question))
            return
        if parsed.path == "/api/trainer/check":
            question_id = str(payload.get("id", ""))
            answer = str(payload.get("answer", ""))[:2000]
            self.send_json(ENGINE.check_trainer_answer(question_id, answer))
            return
        self.send_json({"error": "Маршрут не найден."}, HTTPStatus.NOT_FOUND)

    def serve_static(self, request_path: str) -> None:
        if request_path in {"", "/"}:
            target = PUBLIC_DIR / "index.html"
        else:
            relative = request_path.lstrip("/")
            if relative.startswith("static/"):
                relative = relative.removeprefix("static/")
            target = PUBLIC_DIR / relative
        try:
            target = target.resolve(strict=True)
            target.relative_to(PUBLIC_DIR.resolve())
        except (FileNotFoundError, ValueError):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
        }
        body = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_types.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Server started: http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
