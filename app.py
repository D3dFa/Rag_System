from __future__ import annotations

import json
import math
import os
import random
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "data" / "index.json"
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
    text = "".join(char if ord(char) >= 32 or char in "\t\n\r" else " " for char in text)
    text = re.sub(r"\bker\s*([A-Za-z])\b", r"ker \1", text)
    text = re.sub(r"\bnat\s*ker\s*([A-Za-z])\b", r"nat ker \1", text)
    text = re.sub(r"\bA\s*=\s*ker\s*f\b", "A / ker f", text)
    text = re.sub(r"([А-ЯЁа-яё])-\s*([а-яё])", r"\1\2", text)
    text = re.sub(r"(?<=[а-яё])(?=[А-ЯЁ])", " ", text)
    text = re.sub(r"(?<=[A-Za-z0-9])(?=[А-ЯЁа-яё])", " ", text)
    text = re.sub(r"(?<=[А-ЯЁа-яё])(?=[A-Za-z0-9])", " ", text)
    text = re.sub(r"\b(его|ее|их)(?=элемент)", r"\1 ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bтемсамым\b", "тем самым", text, flags=re.IGNORECASE)
    text = re.sub(r"\bэтовозможно\b", "это возможно", text, flags=re.IGNORECASE)
    text = re.sub(r"\bипрофессорами\b", "и профессорами", text, flags=re.IGNORECASE)
    text = re.sub(r"([.!?])(?=[А-ЯЁA-Z0-9])", r"\1 ", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip()


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


class RagEngine:
    def __init__(self, data: dict):
        self.data = data
        self.chunks = data["chunks"]
        self.stats = data["search"]
        self.df = self.stats["doc_freq"]
        self.avg_len = max(float(self.stats["avg_len"]), 1.0)
        self.n_docs = max(int(self.stats["documents"]), 1)
        self.trainer = {item["id"]: item for item in data.get("trainer", [])}

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
                    "answer": display_text(sentence),
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
                    "answer": display_text(sentence),
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
        for _, sentence, chunk in sentence_scores:
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
        explanation = display_text(explanation)
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
            self.send_json(
                {
                    "source_pdf": data["source_pdf"],
                    "page_count": data["page_count"],
                    "chapter_count": data["chapter_count"],
                    "selected_chapter": data["selected_chapter"],
                    "chapters": data["chapters"],
                    "notes": data["notes"],
                }
            )
            return
        if parsed.path == "/api/trainer/question":
            self.send_json(ENGINE.trainer_question())
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
