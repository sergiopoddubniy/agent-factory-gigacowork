#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Манифест комплекта: состав, хеши, состояния публикации.

    python3 freeze_release.py --папка <путь> --заморозить
    python3 freeze_release.py --папка <путь> --сверить

Код возврата: 0 — состояние подтверждено; 1 — комплект изменился после
заморозки либо манифеста нет; 2 — нечего делать.

**Зачем.** У комплекта нет состояния: «готов» живёт в голове консультанта.
Между «проверили» и «отдали» помещается правка, и снаружи она неотличима от
её отсутствия. У нас это уже случалось: отчёт менялся после того, как состав
был записан. Разбор релиза с тем же дефектом описывает лекарство как цепочку
состояний `building → validated → frozen → verified` и требование, чтобы
выданные байты совпадали с проверенными (arXiv:2607.14155).

**Что делает заморозка.** Считает SHA-256 каждого файла, пишет
`00_МАНИФЕСТ_КОМПЛЕКТА.md` — открытым файлом, таблицей, читаемой человеком, —
и переводит комплект в `frozen`. Сверка пересчитывает и отвечает на один
вопрос: то ли это, что замораживали.

**Чего манифест не делает.** Он не подтверждает правильность содержимого: файл
может быть неизменным и неверным. Он отвечает только за тождество: выдано то,
что проверяли.
"""
from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys
import unicodedata
from datetime import date

nfc = lambda s: unicodedata.normalize("NFC", str(s))
nameflat = lambda s: re.sub(r"[\s_\-]+", " ", nfc(s)).lower().replace("ё", "е")

ИМЯ = "00_МАНИФЕСТ_КОМПЛЕКТА.md"
ПРОПУСК = ("к удалению", "архив", "устарел", "черновик", "draft", ".ds store")


def файлы(root: pathlib.Path):
    out = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name == ИМЯ:
            continue
        rel = p.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if any(x in nameflat(str(rel)) for x in ПРОПУСК):
            continue
        out.append((str(rel), p))
    return out


def хеш(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def отпечаток(записи) -> str:
    """Один хеш на весь комплект: путь + хеш файла, по порядку путей."""
    h = hashlib.sha256()
    for rel, s in sorted(записи):
        h.update(nfc(rel).encode()); h.update(b"\0")
        h.update(s.encode()); h.update(b"\0")
    return h.hexdigest()[:12]


def прочитать(m: pathlib.Path):
    """Манифест → (состояние, отпечаток, {путь: хеш})."""
    t = m.read_text(encoding="utf-8", errors="replace")
    сост = re.search(r"^\*\*Состояние:\*\*\s*`?(\w+)`?", t, re.M)
    отп = re.search(r"^\*\*Отпечаток состава:\*\*\s*`([0-9a-f]+)`", t, re.M)
    записи = dict(re.findall(r"^\|\s*`([^`]+)`\s*\|\s*\d+\s*\|\s*`([0-9a-f]{64})`\s*\|$",
                             t, re.M))
    return (сост.group(1) if сост else "?",
            отп.group(1) if отп else "?", записи)


def записать(root: pathlib.Path, состояние: str, записи) -> pathlib.Path:
    строки = [
        "# Манифест комплекта",
        "",
        "Состав и хеши на момент заморозки. Файл **генерируется**: правки в нём",
        "не имеют смысла, при следующей заморозке он будет перезаписан.",
        "",
        f"**Состояние:** `{состояние}`",
        f"**Дата:** {date.today().isoformat()}",
        f"**Файлов:** {len(записи)}",
        f"**Отпечаток состава:** `{отпечаток([(r, s) for r, s, _ in записи])}`",
        "",
        "Состояния: `building` — комплект собирается; `frozen` — состав",
        "зафиксирован; `verified` — сверка после заморозки прошла. Любое",
        "изменение файла после заморозки возвращает комплект в `building`:",
        "выдавать можно только то, что проверяли.",
        "",
        "| Файл | Байт | SHA-256 |",
        "|---|---|---|",
    ]
    for rel, s, size in sorted(записи):
        строки.append(f"| `{rel}` | {size} | `{s}` |")
    строки.append("")
    m = root / ИМЯ
    m.write_text("\n".join(строки), encoding="utf-8")
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--папка", dest="root", required=True)
    ap.add_argument("--заморозить", dest="freeze", action="store_true")
    ap.add_argument("--сверить", dest="verify", action="store_true")
    a = ap.parse_args()
    root = pathlib.Path(nfc(a.root)).expanduser()
    if not root.is_dir():
        print(f"нет папки: {root}")
        return 2
    if not (a.freeze or a.verify):
        print(__doc__)
        return 2

    текущие = [(rel, хеш(p), p.stat().st_size) for rel, p in файлы(root)]

    if a.freeze:
        m = записать(root, "frozen", текущие)
        print("=" * 64)
        print(f"ЗАМОРОЖЕНО — {root.name}")
        print("=" * 64)
        print(f"\nфайлов: {len(текущие)}")
        print(f"отпечаток состава: {отпечаток([(r, s) for r, s, _ in текущие])}")
        print(f"манифест: {m.name}")
        print("\nПосле заморозки любое изменение обнаруживается сверкой:")
        print("  python3 freeze_release.py --папка <путь> --сверить")
        return 0

    m = root / ИМЯ
    if not m.exists():
        print(f"Манифеста нет: {ИМЯ}. Комплект в состоянии `building` — "
              "сверять не с чем.")
        return 1
    состояние, отп_был, было = прочитать(m)
    стало = {rel: s for rel, s, _ in текущие}

    изменены = sorted(r for r in было if r in стало and было[r] != стало[r])
    исчезли = sorted(r for r in было if r not in стало)
    появились = sorted(r for r in стало if r not in было)

    print("=" * 64)
    print(f"СВЕРКА С МАНИФЕСТОМ — {root.name}")
    print("=" * 64)
    print(f"\nсостояние в манифесте: {состояние}")
    print(f"отпечаток был:  {отп_был}")
    print(f"отпечаток стал: {отпечаток([(r, s) for r, s, _ in текущие])}")

    if not (изменены or исчезли or появились):
        записать(root, "verified", текущие)
        print("\nКомплект тождествен замороженному. Состояние: verified.")
        return 0

    for заголовок, набор in (("ИЗМЕНЕНЫ ПОСЛЕ ЗАМОРОЗКИ", изменены),
                             ("ИСЧЕЗЛИ", исчезли),
                             ("ПОЯВИЛИСЬ", появились)):
        if набор:
            print(f"\n{заголовок} — {len(набор)}")
            for r in набор[:12]:
                print(f"  · {r}")
    записать(root, "building", текущие)
    print("\nКомплект НЕ тождествен замороженному, состояние возвращено в")
    print("`building`. Выдавать можно только то, что проверяли: пройдите")
    print("сверку состава заново и заморозьте снова.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
