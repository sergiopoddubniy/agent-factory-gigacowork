#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Находимость артефактов комплекта: до чего не доходит ни один запрос.

    python3 findability.py --папка <путь> [--топ 10] [--подробно]

Код возврата: 0 — у каждого текстового артефакта есть хотя бы один запрос,
по которому он находится; 1 — есть артефакты с нулевой находимостью.

**Что это.** Мера `retrievability` (Аззопарди и Винай, CIKM 2008): по большому
набору запросов считается, сколько раз документ попал в топ-`c` выдачи. Здесь
запросы порождаются из самого комплекта (термы и биграммы), поиск — BM25.

**Что читать в выводе.** Список артефактов с `r(d) = 0`. Такой файл лежит в
комплекте, но ни один запрос из его собственной предметной области до него не
доходит: для агента и для человека, ищущего по смыслу, он мёртв.

**Чего НЕ читать.** Коэффициент Джини печатается справочно и порогом не
является. На комплекте в два-три десятка документов он неустойчив: одно
переименование заметно двигает значение. Обоснование — отчёт
«Проверка связности выходного пакета», раздел B, где показано, что
находимость и связность по ссылкам — разные величины (ранговая корреляция
0,05–0,15 на коллекциях в миллионы документов), и подменять одну другой
нельзя.

**Граница метода.** Индексируются только текстовые файлы (`.md`, `.txt`).
Спецификации `.docx`, модели `.xlsx` и PDF в индекс не попадают — их
находимость этим инструментом не измеряется, и это честнее, чем считать её
по имени файла.
"""
from __future__ import annotations

import argparse
import math
import pathlib
import re
import sys
import unicodedata
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from textnorm import nfc, токены                    # noqa: E402  общий источник

ТЕКСТ = (".md", ".txt")
ПРОПУСК = ("к удалению", "архив", "устарел", "черновик", ".ds store")


def собрать(root: pathlib.Path):
    docs = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in ТЕКСТ:
            continue
        rel = str(p.relative_to(root))
        if any(x in rel.lower().replace("_", " ") for x in ПРОПУСК):
            continue
        if any(part.startswith(".") for part in p.parts):
            continue
        # Имя файла — часть текста: по нему тоже ищут, и это не хитрость,
        # а то, как человек и агент обращаются к артефакту.
        docs[rel] = токены(rel) + токены(
            p.read_text(encoding="utf-8", errors="replace"))
    return docs


def bm25_индекс(docs):
    df = Counter()
    tf = {}
    длины = {}
    for d, ts in docs.items():
        c = Counter(ts)
        tf[d] = c
        длины[d] = len(ts) or 1
        df.update(c.keys())
    N = len(docs)
    avg = sum(длины.values()) / max(1, N)
    idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
    return tf, длины, idf, df, N, avg


def запросы(df, N, предел=4000):
    """Однословные и двухсловные запросы из самого комплекта.

    Слишком частые термы (встречаются больше чем в половине документов)
    выбрасываются: по ним находится всё, и они ничего не различают.
    """
    одиночные = [t for t, n in df.items() if 2 <= n <= max(2, N // 2)]
    одиночные.sort(key=lambda t: -df[t])
    return [(t,) for t in одиночные[:предел]]


def биграммы(docs, df, N, предел=4000):
    счёт = Counter()
    годен = lambda t: 2 <= df.get(t, 0) <= max(2, N // 2)
    for ts in docs.values():
        for a, b in zip(ts, ts[1:]):
            if годен(a) and годен(b):
                счёт[(a, b)] += 1
    return [q for q, n in счёт.most_common(предел) if n >= 2]


def поиск(q, tf, длины, idf, avg, c, k1=1.2, b=0.75):
    очки = Counter()
    for t in q:
        w = idf.get(t)
        if w is None:
            continue
        for d, c_tf in tf.items():
            f = c_tf.get(t)
            if not f:
                continue
            L = длины[d]
            очки[d] += w * f * (k1 + 1) / (f + k1 * (1 - b + b * L / avg))
    return [d for d, _ in очки.most_common(c)]


def джини(значения) -> float:
    v = sorted(значения)
    n = len(v)
    s = sum(v)
    if n == 0 or s == 0:
        return 0.0
    return sum((2 * i - n - 1) * x for i, x in enumerate(v, 1)) / (n * s)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--папка", dest="root", required=True)
    ap.add_argument("--топ", dest="c", type=int, default=0,
                    help="отсечка ранга; по умолчанию max(5, N//25)")
    ap.add_argument("--подробно", dest="verbose", action="store_true")
    a = ap.parse_args()
    root = pathlib.Path(nfc(a.root)).expanduser()
    if not root.is_dir():
        print(f"нет папки: {root}")
        return 2

    docs = собрать(root)
    if len(docs) < 3:
        print(f"текстовых файлов: {len(docs)} — измерять находимость не на чем")
        return 2

    tf, длины, idf, df, N, avg = bm25_индекс(docs)
    # Отсечка подобрана на двух боевых комплектах: при топ-30 (пятая часть
    # коллекции) мёртвых нет ни у кого — метрика ничего не различает; при
    # топ-5 она сразу указала на реальный дефект. Ниже трёх смысла нет:
    # начинают «умирать» законно узкие документы.
    c = a.c or max(5, N // 25)
    Q = запросы(df, N) + биграммы(docs, df, N)

    r = Counter({d: 0 for d in docs})
    for q in Q:
        for d in поиск(q, tf, длины, idf, avg, c):
            r[d] += 1

    мёртвые = sorted(d for d in docs if r[d] == 0)
    print("=" * 68)
    print(f"НАХОДИМОСТЬ АРТЕФАКТОВ — {root.name}")
    print("=" * 68)
    print(f"\nтекстовых документов: {N} · запросов: {len(Q)} · отсечка: топ-{c}")

    print(f"\nНУЛЕВАЯ НАХОДИМОСТЬ — {len(мёртвые)}")
    for d in мёртвые[:20]:
        print(f"  · {d}")
    if мёртвые:
        print("\n  До этих файлов не доходит ни один запрос из тех, что можно")
        print("  задать по самому комплекту. Причины бывают четыре, и первая")
        print("  на боевых комплектах оказалась самой частой:")
        print("   1) файл существует в НЕСКОЛЬКИХ идентичных копиях — копии")
        print("      конкурируют между собой, и ни одна не выигрывает;")
        print("   2) файл почти пуст;")
        print("   3) он написан словами, которых нет больше нигде;")
        print("   4) он дублирует более полный документ и всегда ему")
        print("      проигрывает.")
        print("  Проверьте первым делом, не лежит ли этот файл в комплекте")
        print("  несколько раз: `md5sum` по совпадающим именам.")

    if a.verbose:
        print("\nСАМЫЕ НАХОДИМЫЕ")
        for d, n in r.most_common(5):
            print(f"  {n:5}  {d}")
        print("\nСАМЫЕ РЕДКИЕ (кроме нулевых)")
        живые = [(d, n) for d, n in r.items() if n]
        for d, n in sorted(живые, key=lambda x: x[1])[:5]:
            print(f"  {n:5}  {d}")

    g = джини(list(r.values()))
    print(f"\nДжини по находимости: {g:.3f} — справочно, порогом не является.")
    print("На комплекте такого размера значение неустойчиво: одно")
    print("переименование заметно его двигает. Смотреть надо список выше.")
    print("\nНе индексировались: .docx, .xlsx, .pdf, .zip — их находимость")
    print("этим инструментом не измеряется.")
    return 1 if мёртвые else 0


if __name__ == "__main__":
    sys.exit(main())
