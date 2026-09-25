#!/usr/bin/env python3
"""hub_gate.py — шлюз запретов для публикации (docs/06_Политика_обезличивания.md).

Единственный источник правил проверки. Ловит признаки клиентского и
провенанса, а не смысл: дата, сумма, домен, ФИО, орг-форма с названием.

    python3 scripts/hub_gate.py --папка <корень> --строго            # витрина
    python3 scripts/hub_gate.py --папка <корень> --мягко             # репозиторий, CI
    python3 scripts/hub_gate.py --папка <корень> --строго --стоп-слова СТОП.txt

Коды возврата: 0 — ноль нарушений; 1 — нарушения; 2 — ошибка входа.
Строгий режим — класс A и класс B (провенанс): даты, суммы, домены вне
белого списка, ФИО, орг-формы с названием, стоп-слова платформы, псевдоним
клиента рядом с датой. Мягкий — только класс A по общим признакам.
Приватный стоп-лист (файл вне Git, по строке на слово) добавляет проверку
по реальным названиям — в репозиторий он не входит намеренно.
"""
from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

EXIT_OK, EXIT_RED, EXIT_INPUT = 0, 1, 2
TEXT_EXT = {".md", ".py", ".txt", ".yaml", ".yml", ".json", ".js", ".html", ".csv"}

# Домены, которые не являются клиентскими: примеры, схемы, публичные реестры и сервисы.
DOMAIN_WHITELIST = {
    "example.ru", "example.com", "w3.org", "schemas.openxmlformats.org", "purl.org",
    "schemas.microsoft.com", "github.com", "img.shields.io", "docs.claude.com",
    "zakupki.gov.ru", "fedresurs.ru", "nalog.gov.ru", "kad.arbitr.ru", "arbitr.ru",
    "developers.sber.ru", "python.org", "cdnjs.cloudflare.com", "cdn.jsdelivr.net",
    "alphaxiv.org", "arxiv.org", "cowork.ru",  # cowork.ru — шаблон адреса платформы: класс B, ловится правилом ПЛАТФОРМА
}
DOMAIN_WHITELIST_PREFIX = ("example-",)

# Признаки. Порядок — по цене ошибки.
RULES_A = [  # запрещено везде
    ("ДОМЕН", re.compile(r"\b((?:[a-z0-9-]+\.)+(?:ru|com|su|io|net|org|рф))\b", re.I)),
    ("ФИО", re.compile(r"\b[А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична)\b")),
    ("ОРГ-ФОРМА", re.compile(r"\b(?:ГК|ПО|ПК|ООО|АО|ЗАО|ПАО|ОАО|ИП|ТД|НПО|НПП|АНО|ФГУП|МУП)\s?[«\"“][^»\"”\n]{2,60}[»\"”]")),
    ("ИНН", re.compile(r"\bИНН\s*[:\s]?\s*\d{10,12}\b")),
]
RULES_B = [  # провенанс — только для витрины
    ("ДАТА", re.compile(r"\b\d{2}\.\d{2}\.20\d{2}\b")),
    ("СУММА", re.compile(r"\b\d[\d  ,.]{2,}\s?(?:₽|руб\.?|млрд|млн\b)")),
    ("ПЛАТФОРМА", re.compile(r"cowork\.ru|<инстанс>\.cowork", re.I)),
    ("ПСЕВДОНИМ+ДАТА", re.compile(r"Клиент-[А-Я][^\n]{0,60}(?<![\d.])\d{2}\.\d{2}(?:\.20\d{2})?\b(?![.\d])|(?<![\d.])\d{2}\.\d{2}(?:\.20\d{2})?\b(?![.\d])[^\n]{0,60}Клиент-[А-Я]")),
]
STOP_PLATFORM = ("Сбер", "СДБ", "Пулково", "GigaChat")  # внутренние названия — везде

# Орг-формы в синтетике примера — разрешённые названия (пример 1С и прогон консолидации).
ORG_ALLOW = ("Северный порт", "Порт-Логистика", "Порт-Сервис", "Порт-Логистик", "Пример-Контрагент",
             "Порт Сервис", "Северный Порт")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def scan(root: Path, strict: bool, stopwords: list[str]) -> list[tuple[str, int, str, str]]:
    hits = []
    rules = list(RULES_A) + (list(RULES_B) if strict else [])
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_EXT or ".git" in p.parts:
            continue
        if "__pycache__" in p.parts or p.name == "hub_gate.py":
            continue  # сам шлюз держит стоп-слова и регулярки
        try:
            lines = nfc(p.read_text(encoding="utf-8")).splitlines()
        except UnicodeDecodeError:
            continue
        rel = str(p.relative_to(root))
        for i, ln in enumerate(lines, 1):
            for name, rx in rules:
                for m in rx.finditer(ln):
                    frag = m.group(0)
                    if name == "ДОМЕН":
                        d = m.group(1).lower()
                        if d in DOMAIN_WHITELIST or d.startswith(DOMAIN_WHITELIST_PREFIX) \
                                or any(d.endswith("." + w) for w in DOMAIN_WHITELIST):
                            continue
                        # версии и числа вида 3.10 / 1.0 ru — не домены
                        if re.fullmatch(r"[\d.]+\.(ru|com|su|io|net|org|рф)", d):
                            continue
                    if name == "ОРГ-ФОРМА" and any(a in frag for a in ORG_ALLOW):
                        continue
                    if name == "СУММА" and re.search(r"токен", ln, re.I):
                        continue  # «1,47 млн токенов» — замер, не сумма сделки
                    hits.append((rel, i, name, frag.strip()))
            for w in STOP_PLATFORM + tuple(stopwords):
                if w and w in ln:
                    hits.append((rel, i, "СТОП-СЛОВО", w))
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Шлюз запретов для публикации")
    ap.add_argument("--папка", dest="root", required=True)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--строго", dest="strict", action="store_true", help="витрина: класс A + провенанс")
    mode.add_argument("--мягко", dest="soft", action="store_true", help="репозиторий: только класс A")
    ap.add_argument("--стоп-слова", dest="stop", default=None, help="приватный файл со стоп-словами (вне Git)")
    ap.add_argument("--исключить", dest="exclude", nargs="*", default=[], help="подпапки, которые не проверяются")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"ОШИБКА ВХОДА: нет папки {root}", file=sys.stderr)
        return EXIT_INPUT
    stop = []
    if args.stop:
        sp = Path(args.stop)
        if not sp.is_file():
            print(f"ОШИБКА ВХОДА: нет файла стоп-слов {sp}", file=sys.stderr)
            return EXIT_INPUT
        stop = [ln.strip() for ln in sp.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    hits = scan(root, args.strict, stop)
    if args.exclude:
        hits = [h for h in hits if not any(h[0].startswith(e.rstrip("/") + "/") or h[0] == e for e in args.exclude)]
    mode_name = "строгий (витрина)" if args.strict else "мягкий (репозиторий)"
    print(f"ШЛЮЗ ЗАПРЕТОВ · режим {mode_name} · {root}")
    by = {}
    for rel, i, name, frag in hits:
        by.setdefault(name, []).append((rel, i, frag))
    for name in sorted(by):
        print(f"\n[{name}] {len(by[name])}")
        for rel, i, frag in by[name][:40]:
            print(f"  {rel}:{i}: {frag[:80]}")
        if len(by[name]) > 40:
            print(f"  … ещё {len(by[name]) - 40}")
    print(f"\nИТОГО нарушений: {len(hits)} → {'ЗЕЛЁНЫЙ' if not hits else 'КРАСНЫЙ'}")
    return EXIT_OK if not hits else EXIT_RED


if __name__ == "__main__":
    sys.exit(main())
