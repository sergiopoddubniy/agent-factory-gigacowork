"""Заголовок навыка GigaCowork — одно место на всю Фабрику.

Контракт файла `SKILL.md`, проверенный пробой на стенде 05.10.2026: форма
«Каталог навыков → Создать навык» при загрузке файла заполняет поля так:

    name         → «Имя навыка»
    description  → «Когда применять»
    тело файла   → «Инструкции»
    category     → «Категория» — значение каталога (`productivity`) НЕ
                   подставилось; в форме пространства свой список
                   (SPACE_CATEGORIES); подставляется ли русское значение —
                   отдельная проба.
    version, tags — полей в форме нет, их читает каталог (стандарт skill-craft).

Лишние ключи импорт пропускает (проба 3б): служебные поля Фабрики — `id`,
`версия агента`, `обновлено`, `спецификация` — остаются в заголовке.

Стандарт каталога (skill-craft): `name`; `description` — одна plain-строка,
без блочного скаляра, без кавычек, без «: » внутри; `category` — одна из семи;
`version` — целое число; `tags` — непустой список slug.

Прежний заголовок Фабрики (`имя навыка`, `когда применять: |`, `категория`,
`version: 1.0.0`) платформа не читала: имя и описание при загрузке оставались
пустыми. Его ключи здесь — только чтобы узнать старый файл и сказать, что
поменять.
"""
from __future__ import annotations

import re
import unicodedata

SPACE_CATEGORIES = (
    "Без категории", "HR", "research", "Аналитика", "Внедрение ИИ", "Закупки",
    "Маркетинг", "Менеджмент", "Поддержка клиентов", "Продажи", "Разработка",
    "Финансы", "Юристы",
)
CATALOG_CATEGORIES = (
    "analytics", "documents", "management", "sales", "research",
    "productivity", "development",
)
# Категория пространства → категория каталога (для выгрузки плагином).
SPACE_TO_CATALOG = {
    "Аналитика": "analytics", "Финансы": "analytics", "research": "research",
    "Закупки": "management", "Менеджмент": "management", "HR": "management",
    "Продажи": "sales", "Маркетинг": "sales", "Юристы": "documents",
    "Разработка": "development", "Поддержка клиентов": "productivity",
    "Внедрение ИИ": "productivity", "Без категории": "productivity",
}
LEGACY_KEYS = ("имя навыка", "когда применять", "категория", "summary")
TRIGGERS_TITLE = "Когда использовать (триггеры)"
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DESC_MIN, DESC_MAX = 80, 500
NEGATIVE = re.compile(r"не для\b|не бери|не брать", re.I)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def plain_line(text: str, limit: int = DESC_MAX) -> str:
    """Одна plain-строка YAML: без переводов строк, без «: », без кавычек в начале."""
    t = re.sub(r"\s+", " ", nfc(text)).strip()
    t = t.replace(": ", " — ").replace(" #", " №")
    t = t.strip(" '\"`|>*&!%@[]{}-")
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:—– ")
    return t


def category_value(raw: str | None) -> str:
    v = nfc(raw or "").strip().strip("«»\"' ")
    for c in SPACE_CATEGORIES:
        if v.lower() == c.lower():
            return c
    # «Разработка 1С» → «Разработка»: категория формы — первое слово значения.
    for c in SPACE_CATEGORIES[1:]:
        if len(c) > 2 and v.lower().startswith(c.lower() + " "):
            return c
    return "Без категории"


def tags_line(*candidates: str) -> str:
    tags: list[str] = []
    for c in candidates:
        for t in re.split(r"[,\s]+", (c or "").lower()):
            t = t.strip("[]«»\"' ")
            if t and SLUG.match(t) and t not in tags:
                tags.append(t)
    return "[" + ", ".join(tags) + "]"


def version_number(version: str | int | None) -> int:
    m = re.search(r"\d+", str(version or ""))
    return int(m.group(0)) if m else 1


def parse(fm: str) -> dict:
    """Верхнеуровневые ключи заголовка: `ключ: значение` (продолжения строк пропускаются)."""
    d: dict[str, str] = {}
    for ln in nfc(fm).splitlines():
        m = re.match(r"^([^\s:#][^:]*?):\s?(.*)$", ln)
        if m:
            d.setdefault(m.group(1).strip().lower(), m.group(2).strip())
    return d


def problems(fm: str, target: str = "space") -> list[str]:
    """Чего не хватает заголовку, чтобы загрузка заполнила поля навыка."""
    d = parse(fm)
    out: list[str] = []
    name = d.get("name", "")
    if not name:
        out.append("нет поля name — из него платформа заполняет «Имя навыка»")
    elif not re.search(r"[А-Яа-яЁё]", name):
        out.append(f"name: {name} — это имя видит пользователь, оно должно быть на его языке, а не слагом")
    elif ": " in name or name[:1] in "'\"|>":
        out.append("name — одна plain-строка, без кавычек и без «: »")
    desc = d.get("description", "")
    if not desc:
        out.append("нет поля description — из него платформа заполняет «Когда применять»")
    elif desc[:1] in "|>":
        out.append("description — блочный скаляр; нужна одна строка, иначе «|» утекает в поле как текст")
    elif desc[:1] in "'\"":
        out.append("description в кавычках — нужна plain-строка")
    elif ": " in desc:
        out.append("в description есть «: » — разбор заголовка на этом падает; перечисления через «—»")
    elif len(desc) < DESC_MIN:
        out.append(f"description короче {DESC_MIN} знаков — это условие отбора навыка, а не пересказ названия")
    elif not NEGATIVE.search(desc):
        out.append("в description нет «не для …» / «не брать …» — навык без границ подхватывается на соседних запросах")
    cat = d.get("category", "")
    allowed = CATALOG_CATEGORIES if target == "catalog" else SPACE_CATEGORIES
    if not cat:
        out.append("нет поля category")
    elif cat not in allowed:
        out.append(f"category: {cat} — не из списка ({', '.join(allowed)})")
    ver = d.get("version", "")
    if not ver:
        out.append("нет поля version — целое число, версия агента")
    elif not ver.isdigit():
        out.append(f"version: {ver} — нужно целое число (версия агента), а не «1.0.0»")
    tags = d.get("tags", "")
    if not tags:
        out.append("нет поля tags — непустой список slug")
    else:
        items = [t.strip(" '\"") for t in tags.strip("[]").split(",") if t.strip()]
        if not items or not tags.startswith("["):
            out.append("tags — список в квадратных скобках: [slug, slug]")
        elif any(not SLUG.match(t) for t in items):
            out.append("tags — только slug: строчная латиница, цифры, дефис")
    old = [k for k in LEGACY_KEYS if k in d]
    if old:
        out.append("поля " + ", ".join(f"«{k}»" for k in old)
                   + " платформа не читает — их значения должны быть в name / description / category")
    return out


def body_triggers_problems(text: str) -> list[str]:
    """Раздел «Когда использовать (триггеры)» в теле: что брать и чего не брать."""
    m = re.search(r"^#{2,4}\s*" + re.escape(TRIGGERS_TITLE.split(" (")[0])
                  + r"[^\n]*\n(.*?)(?=^#{1,4}\s|\Z)", nfc(text), re.M | re.S | re.I)
    if not m:
        return [f"в теле нет раздела «{TRIGGERS_TITLE}» — триггеры по стандарту каталога живут в теле"]
    if not NEGATIVE.search(m.group(1)):
        return [f"в разделе «{TRIGGERS_TITLE}» нет «Не бери навык, если …»"]
    return []
