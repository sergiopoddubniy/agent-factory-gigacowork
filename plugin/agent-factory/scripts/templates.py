#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""База шаблонов агентов: что уже написано и что можно взять целиком.

    python3 templates.py --похожие "свести отчётность группы компаний"
    python3 templates.py --индекс
    python3 templates.py --lint

Код возврата: 0 — порядок; 1 — lint нашёл замечания.

**Чем отличается от реестра агентов.** `agent_registry.py` хранит метаданные и
**путь** к клиентскому комплекту: он отвечает на вопрос «у кого такое уже
было». Здесь лежат **сами артефакты**, готовые к копированию: «вот, берите».
Реестр без базы заставляет каждый раз запрашивать доступ к чужой папке; база
без реестра теряет связь с клиентом, у которого агент работает.

**Почему поиск, а не просто список.** Пять шаблонов читаются глазами,
двадцать — уже нет, а вопрос всегда конкретный: «есть что-нибудь про свод
отчётности?». Поиск идёт по джобе, роли, функции и назначению, поэтому
подходящий шаблон находится **из другой отрасли** — и это главная ценность:
работа финансового блока похожа у металлургов и у пищевиков сильнее, чем
кажется на первой встрече.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import re
import sys
import unicodedata
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parent.parent
БАЗА = ROOT / "references" / "agent_templates"
ИНДЕКС = БАЗА / "ИНДЕКС.md"

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from textnorm import nfc, nameflat, токены          # noqa: E402  общий источник


def читать(p: pathlib.Path) -> str:
    try:
        return nfc(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return ""


def шаблоны() -> list[dict]:
    """Шаблон — папка с навыком внутри. Функция — имя папки уровнем выше."""
    out = []
    if not БАЗА.is_dir():
        return out
    for навык in sorted(БАЗА.rglob("*.md")):
        имя = nameflat(навык.name)
        if not (имя.startswith("00 навык") or имя == "skill.md"):
            continue
        папка = навык.parent
        txt = читать(навык)
        фм = txt.split("---", 2)[1] if txt.startswith("---") else ""

        def поле(k: str) -> str:
            m = re.search(rf"^\s*{k}\s*:\s*(.+?)\s*$", фм, re.M | re.I)
            return m.group(1).strip() if m else ""

        h1 = re.search(r"^#\s+(.+?)\s*$", txt, re.M)
        когда = ""
        m = re.search(r"^когда применять:\s*\|?\s*$(.*?)^\w+:", фм, re.M | re.S)
        if m:
            когда = " ".join(m.group(1).split())
        назн = re.search(r"\n#{2,}\s*1\.?\s*[Нн]азначени[ея][^\n]*\n(.*?)(?=\n#{2,}\s|\Z)",
                         txt, re.S)
        функция = папка.parent.name if папка.parent != БАЗА else поле("категория")
        карта = папка / "КАРТА_ПЕРЕНОСА.md"
        out.append({
            "slug": папка.name,
            "имя": (h1.group(1).strip() if h1 else папка.name),
            "функция": функция,
            "роль": поле("роль"),
            "джоба": поле("джоба"),
            "когда": когда[:400],
            "назначение": " ".join((назн.group(1) if назн else "").split())[:400],
            "путь": str(папка.relative_to(ROOT)),
            "карта": карта.exists(),
            "происхождение": поле("происхождение"),
        })
    return out


def похожие(запрос: str, записи: list[dict], k: int = 5):
    """BM25 с весом на короткие поля: роль и функция иначе не слышны."""
    def док(z):
        части = [z["имя"], z["джоба"], z["когда"], z["назначение"], z["slug"]]
        части += [z["роль"]] * 4
        части += [z["функция"]] * 3
        return " ".join(части)

    docs = {z["slug"]: токены(док(z)) for z in записи}
    if not docs:
        return []
    df = Counter()
    for ts in docs.values():
        df.update(set(ts))
    N = len(docs)
    avg = sum(len(t) for t in docs.values()) / N
    q = токены(запрос)
    оценки = []
    for z in записи:
        ts = docs[z["slug"]]
        tf = Counter(ts)
        s = 0.0
        for w in q:
            if w not in tf:
                continue
            idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
            s += idf * tf[w] * 2.2 / (tf[w] + 1.2 * (0.25 + 0.75 * len(ts) / avg))
        if s > 0:
            оценки.append((z, s))
    оценки.sort(key=lambda x: -x[1])
    return оценки[:k]


def собрать_индекс(записи: list[dict]) -> str:
    по_функциям: dict[str, list[dict]] = {}
    for z in записи:
        по_функциям.setdefault(z["функция"] or "без функции", []).append(z)
    строки = [
        "# База шаблонов агентов — индекс",
        "",
        "> **Файл генерируется.** Пересобрать: `python3 scripts/templates.py --индекс`.",
        "",
        f"Шаблонов: **{len(записи)}** по {len(по_функциям)} функциям.",
        "",
        "Зачем: перед проектированием нового агента взять готовый и доработать",
        "по карте переноса, а не писать с нуля. Поиск — `--похожие \"<джоба>\"`.",
        "",
    ]
    for функция, лист in sorted(по_функциям.items()):
        строки += [f"## {функция}", "",
                   "| Шаблон | Роль | Джоба | Карта переноса |",
                   "|---|---|---|---|"]
        for z in sorted(лист, key=lambda x: x["slug"]):
            строки.append(f"| `{z['slug']}` — {z['имя']} | {z['роль'] or '—'} | "
                          f"{(z['джоба'] or z['когда'])[:90] or '—'} | "
                          f"{'есть' if z['карта'] else '**НЕТ**'} |")
        строки.append("")
    строки += [
        "---",
        "",
        "## Чего здесь нет",
        "",
        "Признаков принадлежности клиенту. Данные шаблона — либо синтетика,",
        "либо обезличенный набор; правила и пороги в нём **чужие** и подлежат",
        "замене по карте переноса. Шаблон, перенесённый целиком без замены, даёт",
        "«похоже и неверно» — худший исход, потому что выглядит готовой работой.",
        "",
    ]
    return "\n".join(строки)


def lint(записи: list[dict]) -> list[str]:
    из_за = []
    for z in записи:
        if not z["карта"]:
            из_за.append(f"{z['slug']}: нет `КАРТА_ПЕРЕНОСА.md` — шаблон "
                         "скопируют целиком, вместе с чужими порогами")
        if not (z["джоба"] or z["когда"]):
            из_за.append(f"{z['slug']}: нечем найти — ни джобы, ни «когда "
                         "применять»")
        if z["происхождение"] and z["происхождение"].lower() not in (
                "синтетика", "обезличено"):
            из_за.append(f"{z['slug']}: происхождение «{z['происхождение']}» — "
                         "в базе допустимы только «синтетика» и «обезличено»")
    if not записи:
        из_за.append("база пуста: переиспользовать нечего, и Стадия 0 будет "
                     "каждый раз собирать с нуля")
    return из_за


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--похожие", dest="query", default=None)
    ap.add_argument("--индекс", dest="idx", action="store_true")
    ap.add_argument("--lint", dest="lint", action="store_true")
    a = ap.parse_args()
    if not (a.query or a.idx or a.lint):
        print(__doc__)
        return 2

    записи = шаблоны()

    if a.query:
        найдено = похожие(a.query, записи)
        print(f"\nПОХОЖИЕ ШАБЛОНЫ НА «{a.query}» — {len(найдено)}\n")
        if not найдено:
            print("  Ничего не нашлось. Это нормальный ответ: собирай с нуля и")
            print("  запиши в комплект, что кандидатов не было (Я21).\n")
            return 0
        for z, s in найдено:
            print(f"  [{s:5.1f}] {z['slug']} — {z['имя']}")
            print(f"          функция: {z['функция'] or '—'} · роль: {z['роль'] or '—'}")
            if z["джоба"] or z["когда"]:
                print(f"          {(z['джоба'] or z['когда'])[:150]}")
            print(f"          путь: {z['путь']}")
            print(f"          карта переноса: "
                  f"{'есть — читай её до копирования' if z['карта'] else 'НЕТ, копировать вслепую нельзя'}")
            print()
        print("  Что делать: взять папку шаблона целиком, пройти карту переноса")
        print("  и записать в комплект, что взято и что отвергнуто (Я21).\n")
        return 0

    if a.idx:
        БАЗА.mkdir(parents=True, exist_ok=True)
        ИНДЕКС.write_text(собрать_индекс(записи), encoding="utf-8")
        print(f"шаблонов: {len(записи)} · индекс: {ИНДЕКС}")
        return 0

    замечания = lint(записи)
    print(f"\nLINT — {len(замечания)}")
    for z in замечания:
        print(f"  · {z}")
    return 1 if замечания else 0


if __name__ == "__main__":
    sys.exit(main())
