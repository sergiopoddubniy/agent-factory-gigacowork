#!/usr/bin/env python3
"""
factory_common.py — общий разбор спецификации агента (AGENT_SPEC.md).

Спецификация — один markdown-файл с фронтматтером и пятнадцатью
нумерованными блоками. Разбор здесь один на все скрипты фабрики, чтобы
ворота, план добора, сборка и пакет доказательств читали спецификацию
одинаково: расхождение в разборе — это расхождение в вердиктах.

Только stdlib. Совместимо с Python 3.9+.
"""
from __future__ import annotations

import hashlib
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

EXIT_OK, EXIT_RED, EXIT_INPUT = 0, 1, 2

# Канонические блоки спецификации: номер → заголовок.
BLOCKS = {
    1: "Возможность",
    2: "PR/FAQ",
    3: "Гипотеза результата",
    4: "Адаптация или перепроектирование",
    5: "Пользователь и триггер",
    6: "Входы и выходы",
    7: "Периметр",
    8: "Правила и критерии решений",
    9: "Неопределённость и эскалация",
    10: "HITL-карта",
    11: "Доступ и guardrails",
    12: "Приёмочные случаи",
    13: "Эталонные разборы",
    14: "Переиспользование",
    15: "Владелец и приёмка",
}

# Поля фронтматтера и допустимые значения там, где они перечислимы.
FM_REQUIRED = ["id", "имя агента", "версия спецификации", "обновлено",
               "класс применимости", "контур", "уровень доступа"]
CLASSES = ["детерминированный", "ии-помощник", "ии-нативный", "агентный"]
CONTOURS = ["demo", "pilot", "production"]
ACCESS = ["L0", "L1", "L2", "L3"]

# Поля блока 3 «Гипотеза результата».
HYPOTHESIS_FIELDS = ["Метрика", "Бейзлайн", "Цель", "Опережающий индикатор",
                     "Период подтверждения", "Условие пересмотра"]

# Обязательные идентификаторы приёмочных случаев (те же, что проверяет
# сборщик пакета GigaCowork).
REQUIRED_CASES = ["happy_path", "missing_required_input", "conflicting_sources",
                  "prompt_injection_in_document",
                  "external_action_without_confirmation"]

STATUS_FILLED, STATUS_ASSUMED, STATUS_MISSING = "заполнено", "допущение", "отсутствует"

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)+$")
HEADING_RE = re.compile(r"^##\s+(\d{1,2})\.\s*(.+?)\s*$")


def nfc(s: str) -> str:
    """macOS хранит «ё» и «й» разложенными; сравнивать можно только NFC."""
    return unicodedata.normalize("NFC", s)


def read_text(path: Path, label: str = "файл") -> str:
    """Отсутствующий файл — внятный отказ с кодом 2, а не traceback."""
    try:
        return nfc(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"ОШИБКА ВХОДА: не найден {label}: {path}", file=sys.stderr)
        sys.exit(EXIT_INPUT)
    except UnicodeDecodeError:
        print(f"ОШИБКА ВХОДА: {label} не читается как UTF-8: {path}", file=sys.stderr)
        sys.exit(EXIT_INPUT)


def fingerprint(text: str) -> str:
    """Отпечаток содержимого: восемь hex-знаков SHA-256 по NFC-тексту без
    хвостовых пробелов строк. Один и тот же смысл — один отпечаток."""
    norm = "\n".join(ln.rstrip() for ln in nfc(text).splitlines()).strip() + "\n"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:8]


@dataclass
class Block:
    number: int
    title: str
    text: str
    status: str = STATUS_MISSING
    assumptions: list = field(default_factory=list)
    unknowns: list = field(default_factory=list)

    @property
    def lines(self) -> list:
        return [ln.rstrip() for ln in self.text.splitlines()]

    def field_value(self, name: str) -> str | None:
        """Значение строки вида «Имя: значение» внутри блока."""
        pat = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?" + re.escape(name)
                         + r"(?:\*\*)?\s*:\s*(.*)$", re.I)
        for ln in self.lines:
            m = pat.match(ln)
            if m:
                return m.group(1).strip()
        return None

    def bullets(self) -> list:
        return [re.sub(r"^\s*[-*]\s+", "", ln).strip()
                for ln in self.lines if re.match(r"^\s*[-*]\s+", ln)]

    def table_rows(self) -> list:
        """Строки markdown-таблицы без заголовка и разделителя."""
        rows = []
        for ln in self.lines:
            if not ln.strip().startswith("|"):
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            rows.append(cells)
        return rows[1:] if rows else []

    def subsection(self, name: str) -> list:
        """Строки после маркера «Имя:» до следующего маркера того же вида.
        Используется для In-scope / Out-of-scope, Входы / Выходы, Шаги."""
        out, on = [], False
        marker = re.compile(r"^\s*(?:\*\*)?([A-Za-zА-Яа-яЁё/ -]+?)(?:\*\*)?\s*:\s*(.*)$")
        for ln in self.lines:
            m = marker.match(ln)
            if m and not re.match(r"^\s*[-*]\s+", ln):
                key = nfc(m.group(1)).strip().lower()
                on = key == nfc(name).lower()
                if on and m.group(2).strip():
                    out.append(m.group(2).strip())
                continue
            if on and ln.strip():
                out.append(re.sub(r"^\s*[-*]\s+", "", ln).strip())
        return out


@dataclass
class Spec:
    path: Path
    raw: str
    frontmatter: dict
    blocks: dict  # number → Block
    problems: list  # структурные проблемы разбора

    @property
    def fp(self) -> str:
        return fingerprint(self.raw)

    def fm(self, key: str, default: str = "") -> str:
        return self.frontmatter.get(nfc(key).lower(), default)

    def block(self, n: int) -> Block:
        return self.blocks.get(n) or Block(n, BLOCKS[n], "", STATUS_MISSING)

    @property
    def slug(self) -> str:
        return self.fm("id")

    @property
    def klass(self) -> str:
        return self.fm("класс применимости").lower()


def parse_frontmatter(text: str) -> tuple[dict, str, list]:
    problems = []
    if not text.lstrip().startswith("---"):
        return {}, text, ["нет YAML-фронтматтера в начале файла"]
    parts = text.lstrip().split("---", 2)
    if len(parts) < 3:
        return {}, text, ["фронтматтер не закрыт второй строкой «---»"]
    fm = {}
    for ln in parts[1].splitlines():
        if not ln.strip() or ln.strip().startswith("#"):
            continue
        m = re.match(r"^\s*([^:]+?)\s*:\s*(.*)$", ln)
        if not m:
            problems.append(f"строка фронтматтера не разобрана: «{ln.strip()}»")
            continue
        fm[nfc(m.group(1)).strip().lower()] = m.group(2).strip().strip("\"'")
    return fm, parts[2], problems


def classify_block(text: str) -> tuple[str, list, list]:
    """Статус блока: отсутствует (пусто), допущение (есть строка
    «Допущение:»), заполнено. Строки «Не знаем:» — явные пробелы; они не
    делают блок заполненным, но и не считаются молчанием."""
    assumptions, unknowns, content = [], [], []
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        m = re.match(r"^(?:[-*]\s*)?(?:\*\*)?Допущение(?:\*\*)?\s*:\s*(.*)$", s, re.I)
        if m:
            assumptions.append(m.group(1).strip())
            continue
        m = re.match(r"^(?:[-*]\s*)?(?:\*\*)?Не знаем(?:\*\*)?\s*:\s*(.*)$", s, re.I)
        if m:
            unknowns.append(m.group(1).strip())
            continue
        if s in ("—", "-", "…", "..."):
            continue
        content.append(s)
    if not content and not assumptions:
        return STATUS_MISSING, assumptions, unknowns
    if assumptions and not content:
        return STATUS_ASSUMED, assumptions, unknowns
    if assumptions:
        return STATUS_ASSUMED, assumptions, unknowns
    return STATUS_FILLED, assumptions, unknowns


def parse_spec(path: Path) -> Spec:
    raw = read_text(path, "спецификация")
    fm, body, problems = parse_frontmatter(raw)
    for key in FM_REQUIRED:
        if not fm.get(key):
            problems.append(f"фронтматтер: нет поля «{key}»")
    if fm.get("id") and not ID_RE.match(fm["id"]):
        problems.append("фронтматтер: id должен быть вида <клиент>-<джоба>, "
                        "латиница строчными через дефис")
    if fm.get("класс применимости") and fm["класс применимости"].lower() not in CLASSES:
        problems.append("фронтматтер: класс применимости — один из: " + ", ".join(CLASSES))
    if fm.get("контур") and fm["контур"] not in CONTOURS:
        problems.append("фронтматтер: контур — один из: " + ", ".join(CONTOURS))
    if fm.get("уровень доступа") and fm["уровень доступа"] not in ACCESS:
        problems.append("фронтматтер: уровень доступа — один из: " + ", ".join(ACCESS))

    blocks, current, buf = {}, None, []

    def flush():
        if current is None:
            return
        n, title = current
        text = "\n".join(buf).strip("\n")
        status, ass, unk = classify_block(text)
        blocks[n] = Block(n, title, text, status, ass, unk)

    for ln in body.splitlines():
        m = HEADING_RE.match(ln)
        if m:
            flush()
            n = int(m.group(1))
            current, buf = (n, m.group(2)), []
            if n not in BLOCKS:
                problems.append(f"блок {n} не входит в схему спецификации")
            elif nfc(m.group(2)).lower() != nfc(BLOCKS[n]).lower():
                problems.append(f"блок {n}: заголовок «{m.group(2)}» отличается от "
                                f"канонического «{BLOCKS[n]}»")
            continue
        if current is not None:
            buf.append(ln)
    flush()
    for n in BLOCKS:
        if n not in blocks:
            blocks[n] = Block(n, BLOCKS[n], "", STATUS_MISSING)
    return Spec(path, raw, fm, blocks, problems)


def case_ids(block: Block) -> list:
    """Идентификаторы приёмочных случаев: `id` в обратных кавычках в начале
    пункта списка."""
    ids = []
    for b in block.bullets():
        m = re.match(r"^`([a-z0-9_]+)`", b)
        if m:
            ids.append(m.group(1))
    return ids


def ears_lines(block: Block) -> list:
    """Строки в форме EARS: «Когда <условие>, агент должен <ответ>»."""
    out = []
    for ln in block.lines:
        s = re.sub(r"^\s*[-*]\s+", "", ln).strip()
        if re.match(r"^(Когда|Если|Пока)\b", s, re.I) and re.search(r"\bдолжен\b", s, re.I):
            out.append(s)
    return out


def hitl_rows(block: Block) -> list:
    """Строки HITL-карты: пять колонок — точка, кто, критерий, что
    блокирует, эскалация."""
    return [r for r in block.table_rows() if len(r) >= 5 and any(c for c in r)]


def scope_lists(block: Block) -> tuple[list, list]:
    return block.subsection("In-scope"), block.subsection("Out-of-scope")


def io_lists(block: Block) -> tuple[list, list]:
    return block.subsection("Входы"), block.subsection("Выходы")


def steps(block: Block) -> list:
    """Нумерованные шаги в блоке 8 после маркера «Шаги:»."""
    out, on = [], False
    for ln in block.lines:
        if re.match(r"^\s*(?:\*\*)?Шаги(?:\*\*)?\s*:", ln):
            on = True
            continue
        if on:
            if re.match(r"^\s*(?:\*\*)?Чек-лист полноты(?:\*\*)?\s*:", ln, re.I):
                break
            m = re.match(r"^\s*\d+[.)]\s+(.*)$", ln)
            if m:
                out.append(m.group(1).strip())
            elif ln.strip() and not ln.startswith(" "):
                on = False
    return out


SUBMARKER_RE = re.compile(r"^\s*(?:\*\*)?(Шаги|Чек-лист полноты)(?:\*\*)?\s*:", re.I)


def rules(block: Block) -> list:
    """Правила решений: пункты списка до первого подмаркера («Шаги:»,
    «Чек-лист полноты:»)."""
    out = []
    for ln in block.lines:
        if SUBMARKER_RE.match(ln):
            break
        if re.match(r"^\s*[-*]\s+", ln):
            out.append(re.sub(r"^\s*[-*]\s+", "", ln).strip())
    return out


def checklist(block: Block) -> list:
    """Чек-лист полноты (блок 8, подмаркер «Чек-лист полноты:»): пункты,
    по одному на строку результата. Это единица результата для реестра
    вопросов (output_stability, уровень 1): сколько пунктов — столько строк,
    в любом прогоне."""
    out, on = [], False
    for ln in block.lines:
        if re.match(r"^\s*(?:\*\*)?Чек-лист полноты(?:\*\*)?\s*:", ln, re.I):
            on = True
            continue
        if on:
            if SUBMARKER_RE.match(ln):
                break
            m = re.match(r"^\s*(?:\d+[.)]|[-*])\s+(.*)$", ln)
            if m:
                out.append(m.group(1).strip())
            elif ln.strip() and not ln.startswith(" "):
                break
    return out


CRITERION_RE = re.compile(r"\s+[—–-]\s+закрыто,?\s+если\s*:?\s+", re.I)


def checklist_items(block: Block) -> list:
    """Пункты чек-листа с критерием закрытия: [(пункт, критерий)]. Форма
    пункта в спецификации — «<пункт> — закрыто, если: <критерий>». Критерий
    решает неоднозначность заранее (output_stability, уровень 4): статус
    `закрыто` ставится только по нему, косвенное упоминание в источнике
    критерий не выполняет. Пункт без критерия — критерий пустая строка."""
    out = []
    for raw in checklist(block):
        parts = CRITERION_RE.split(raw, maxsplit=1)
        item = parts[0].strip().rstrip(".")
        crit = parts[1].strip().rstrip(".") if len(parts) > 1 else ""
        out.append((item, crit))
    return out


# ---------------------------------------------------------------------------
# Расширения спецификации под целевой состав поставки (delivery_composition.md)
# ---------------------------------------------------------------------------

def cases(block: Block) -> list:
    """Приёмочные случаи целиком: [{id, input, expect, features}].

    Пункт блока 12: `` `id` — Вход: … → Ожидание: … · Признаки: есть: X; нет: Y ``.
    «Признаки» — машинные признаки на языке `check_run.py` (deployed_agent_evals.md),
    через точку с запятой; необязательны — сборщик подставит признаки по умолчанию
    для обязательных случаев."""
    out = []
    for b in block.bullets():
        m = re.match(r"^`([a-z0-9_]+)`\s*[—–-]\s*(.*)$", b)
        if not m:
            continue
        cid, rest = m.group(1), m.group(2)
        feats = []
        fm = re.search(r"[·•]\s*Признаки\s*:\s*(.*)$", rest, re.I)
        if fm:
            rest = rest[:fm.start()].strip()
            for chunk in re.split(r"\s*;\s*", fm.group(1).strip()):
                mm = re.match(r"^([а-яa-z\-]+(?:>=)?)\s*:\s*(.*)$", chunk.strip(), re.I)
                if mm:
                    feats.append((mm.group(1).lower(), mm.group(2).strip()))
        inp, _, exp = rest.partition("→")
        inp = re.sub(r"^\s*Вход\s*:\s*", "", inp, flags=re.I).strip()
        exp = re.sub(r"^\s*Ожидание\s*:\s*", "", exp, flags=re.I).strip()
        out.append({"id": cid, "input": inp, "expect": exp, "features": feats})
    return out


def commands_list(block: Block) -> list:
    """Команды агента из блока 6, подмаркер «Команды:». Пункт:
    `<имя> — <описание> — Подать: <что> → Получить: <что>`.
    Если подмаркера нет — команды только служебные (N.0 «Как со мной
    работать» и N.1 запуск), их порождает сборщик."""
    out = []
    for ln in block.subsection("Команды"):
        parts = [p.strip() for p in re.split(r"\s+[—–]\s+", ln)]
        name = parts[0] if parts else ln
        desc = parts[1] if len(parts) > 1 else ""
        give, get = "", ""
        for p in parts[2:]:
            m = re.match(r"^Подать\s*:\s*(.*?)(?:\s*→\s*Получить\s*:\s*(.*))?$", p, re.I)
            if m:
                give, get = m.group(1).strip(), (m.group(2) or "").strip()
        if name:
            out.append({"name": name, "desc": desc, "give": give, "get": get})
    return out


def kb_files(block: Block) -> list:
    """Состав базы знаний пространства — блок 6, подмаркер «База знаний:».
    Пункт: `<имя файла> — <что это и откуда взять>`. Это то, что грузится в
    пространство ДО первой задачи (методика, шаблоны, справочники) — в
    отличие от входов, которые приносит пользователь с каждым запросом."""
    out = []
    for ln in block.subsection("База знаний"):
        name, _, desc = ln.partition(" — ")
        out.append({"file": name.strip().strip("`"), "desc": desc.strip()})
    return out


CORE_KINDS = ("нет", "расчётное", "извлекающее")
EXECUTORS = ("код", "модель", "человек")


def boundary_rows(block: Block) -> list:
    """Таблица «шаг → исполнитель → инвариант → чем проверяется» — блок 8,
    подмаркер «Граница код/модель:». Строка: `<шаг> — <исполнитель> —
    <инвариант или нет> — <чем проверяется>`; исполнитель — код / модель /
    человек (допустимо «код+модель»). Решение о детерминированном слое
    принимается по шагам, а не по агенту (agent_package.md, «Тест на
    исполнителя»)."""
    out = []
    for ln in block.subsection("Граница код/модель"):
        parts = [x.strip() for x in re.split(r"\s+[—–]\s+", ln)]
        if not parts or not parts[0]:
            continue
        ex = nfc(parts[1]).lower() if len(parts) > 1 else ""
        out.append({"step": parts[0], "executor": ex,
                    "invariant": parts[2] if len(parts) > 2 else "",
                    "check": parts[3] if len(parts) > 3 else "",
                    "code": "код" in ex, "complete": len(parts) >= 4})
    return out


def core_kind(block: Block) -> dict:
    """Решение о ядре агента — блок 6, строка «Ядро:». Значения:
    `нет — причина: <…>` (инварианта нет либо исполнять нечем),
    `расчётное` (арифметический инвариант: консолидация, сверка, начисления),
    `извлекающее` (адресный инвариант и полнота: парсер источника со
    стабильной структурой). Возвращает {"kind": ..., "reason": ..., "raw": ...};
    kind == "" — строки нет. Повод: агент реестра закупок собрали как
    «модель читает страницу», хотя задачу решал скрипт — критерий читался
    как «только арифметика», а решение «ядра не будет» нигде не стояло явно."""
    lines = block.subsection("Ядро")
    if not lines:
        return {"kind": "", "reason": "", "raw": ""}
    raw = " ".join(lines).strip()
    low = nfc(raw).lower().replace("расчетное", "расчётное")
    kind = next((k for k in CORE_KINDS if low.startswith(k)), "?")
    reason = ""
    m = re.search(r"причина\s*:\s*(.+)$", raw, re.I)
    if m:
        reason = m.group(1).strip()
    return {"kind": kind, "reason": reason, "raw": raw}


def workspace_skills(block: Block) -> list:
    """Навыки пространства (коннекторы и общие навыки), от которых зависит
    агент — блок 11, подмаркер «Навыки пространства:». Пункт: `<имя навыка>
    — <зачем>`. Порождает раздел 0 навыка и блок активации карточки
    (agent_package.md, «0. Обязательная активация навыков»)."""
    out = []
    for ln in block.subsection("Навыки пространства"):
        if re.match(r"^(нет|отсутствуют|не требуются|—)\b", ln.strip(), re.I):
            continue
        name, _, why = ln.partition(" — ")
        name = name.strip().strip("«»\"'`")
        if name:
            out.append({"name": name, "why": why.strip()})
    return out


def connectors(block: Block) -> list:
    """Коннекторы — блок 11, подмаркер «Коннекторы:». Пункт: `<имя> — <что
    даёт> — версия <x.y>`; «нет» или пустой список — коннекторов нет, и это
    решение сборщик пишет в карточку явно."""
    out = []
    for ln in block.subsection("Коннекторы"):
        if re.match(r"^(нет|отсутствуют|не требуются)\b", ln.strip(), re.I):
            continue
        parts = [p.strip() for p in re.split(r"\s+[—–]\s+", ln)]
        out.append({"name": parts[0].strip("`«»\"'"), "what": parts[1] if len(parts) > 1 else "",
                    "version": next((p for p in parts[2:] if re.search(r"верси", p, re.I)), "")})
    return out
