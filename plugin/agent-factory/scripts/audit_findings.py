#!/usr/bin/env python3
"""
audit_findings.py — сверка журнала находок с содержимым скилла.

Зачем. Находка, записанная в журнал и не внесённая в правила, — это
проделанная работа, лежащая рядом с результатом. Так уже произошло трижды:
`output_stability.md`, `computational_core.md` и `eval_harness.md` были
описаны, слинкованы и ни в одном шаге пайплайна не названы. Каждый раз это
обнаруживалось случайно, при разборе следующего прогона.

Что делает. Для каждой строки `references/evidence_log.md` берёт адрес
правки (файл, указанный во второй колонке) и проверяет, есть ли в этом файле
след формулировки находки. След ищется по характерным словам, а не по
точному совпадению: формулировка в журнале и в правиле обычно разная.

Две точности проверки, и разница принципиальна.

ТОЧНО — когда адрес правки в журнале указывает файл И раздел:
`agent_package.md`, раздел «Границы агента». Тогда проверяется наличие
именно этого заголовка: он либо есть, либо нет.

ГРУБО — когда указан только файл. Тогда ищутся характерные слова находки, и
чувствительность низкая: замерено, что вырезанный целиком раздел правила так
НЕ ловится — слова рассыпаны по файлу и находятся в других местах. Такие
строки помечаются отдельно; для них проверка означает «тема в файле
присутствует», не более.

Отсюда требование к формату журнала: **у новой записи адрес правки содержит
заголовок раздела**. Это работа на десять секунд при записи и разница между
проверкой и её видимостью.

Запуск:
    python3 scripts/audit_findings.py             # всё
    python3 scripts/audit_findings.py --repeated  # только статус «Повторено»

Код возврата: 0 — кандидатов на пропуск нет, 1 — есть.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REFS = ROOT / "references"
LOG = REFS / "evidence_log.md"

nfc = lambda s: unicodedata.normalize("NFC", s)
norm = lambda s: re.sub(r"\s+", " ", nfc(s)).strip().lower().replace("ё", "е")

# Слова, которые есть везде и следом быть не могут.
STOP = set("""агент агента агенту агентов навык навыка скилл скилла файл файла
это того этого который которые когда чтобы более менее если тогда после перед
есть быть было были нет при для как что так там уже ещё еще один одна одно два
все всё всех тот та то они его их не на по из во от до над под без про или
клиент клиента клиенту прогон прогона прогоне проверка проверки правило""".split())


def keywords(text: str, n: int = 6) -> list[str]:
    """Характерные слова находки: длинные, не служебные, без разметки."""
    t = norm(re.sub(r"`[^`]*`|\*\*|«|»", " ", text))
    words = [w for w in re.findall(r"[а-яa-z]{5,}", t) if w not in STOP]
    seen, out = set(), []
    for w in words:
        stem = w[:6]                      # грубая нормализация окончаний
        if stem in seen:
            continue
        seen.add(stem)
        out.append(stem)
    return out[:n]


def parse_log() -> list[dict]:
    rows = []
    for line in nfc(LOG.read_text(encoding="utf-8")).splitlines():
        if not line.startswith("|"):
            continue
        c = [x.strip() for x in line.strip("|").split("|")]
        if len(c) < 4 or set(c[0]) <= set("-: ") or c[0] in ("Находка", "Статус"):
            continue
        if "Что означает" in line or c[0] == "Утверждение":
            continue                       # шапки легенды и таблицы источников
        rows.append({"finding": c[0], "where": c[1],
                     "evidence": c[2], "status": re.sub(r"\*", "", c[-1])})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeated", action="store_true",
                    help="только находки со статусом «Повторено»")
    args = ap.parse_args()

    rows = parse_log()
    if args.repeated:
        rows = [r for r in rows if "Повторено" in r["status"]]

    texts = {p.name: norm(p.read_text(encoding="utf-8"))
             for p in [ROOT / "SKILL.md", *REFS.glob("*.md"), *(ROOT / "scripts").glob("*.py")]}
    # В фабрике оркестратор разнесён: SKILL.md держит только маршрутизацию и
    # правила, применяемые всегда, а пайплайны режимов лежат в
    # references/pipelines/*.md. Адрес «SKILL.md» в журнале находок относится к
    # оркестратору целиком — иначе каждый перенос стадии в пайплайн выглядел бы
    # как пропавшая находка.
    for p in sorted((REFS / "pipelines").glob("*.md")):
        texts["SKILL.md"] += "\n" + norm(p.read_text(encoding="utf-8"))

    # Находка может относиться не к скиллу, а к платформе или к стенду теста.
    # Такая по построению не встраивается, и требовать от неё адреса правки —
    # значит плодить ложные срабатывания.
    #
    # Оговорка проверяется ДО разбора адреса. Первая версия смотрела её
    # только там, где файл не назван, — а записи вида «не скилл — измеритель
    # стенда `check_run.py`» файл называют. Они уходили в грубый режим,
    # искали `check_run.py` среди файлов скилла, не находили и попадали в
    # «кандидаты на пропуск». Два дня подряд регулярная задача выносила их
    # человеку как требующие решения, хотя решение было принято и записано
    # прямо в адресе.
    OUT_OF_SCOPE = (r"не скилл|не дефект скилла|поведени\w* платформы|"
                    r"стенд\w* теста|измеритель стенда")

    suspects, noaddr, vague, outside = [], [], [], []
    for r in rows:
        if re.search(OUT_OF_SCOPE, r["where"], re.I):
            outside.append(r)
            continue
        files = re.findall(r"`([A-Za-z_]+\.(?:md|py))`", r["where"])
        if not files:
            noaddr.append(r)
            continue
        # Адрес указывает на файл, которого в скилле нет, и это не оговорено:
        # либо опечатка в имени, либо файл переименовали и адрес не обновили.
        # Это не «след не найден», а «искать негде» — разные вещи.
        if not any(f in texts for f in files):
            r["why"] = f"файла {', '.join(files)} в скилле нет — адрес устарел"
            suspects.append(r)
            continue
        # Точный режим: в адресе назван раздел — проверяем наличие заголовка.
        sect = re.search(r"раздел[^«]*«([^»]{4,60})»", r["where"])
        if sect:
            needle = norm(sect.group(1))
            r["mode"] = "точно"
            if not any(needle in texts.get(f, "") for f in files):
                r["why"] = f"раздела «{sect.group(1)}» в файле нет"
                suspects.append(r)
            continue
        # Грубый режим: указан только файл. Чувствительность низкая.
        kws = keywords(r["finding"])
        best = max((sum(1 for k in kws if k in texts.get(f, "")) for f in files), default=0)
        r["mode"] = "грубо"
        vague.append(r)
        if best < max(3, (len(kws) + 1) // 2):
            r["why"] = "темы находки в файле не видно"
            suspects.append(r)

    print("=" * 66)
    print(f"АУДИТ НАХОДОК — {len(rows)} строк журнала")
    print("=" * 66)

    if noaddr:
        print(f"\nБЕЗ АДРЕСА ПРАВКИ — {len(noaddr)}")
        print("  Находка без указания файла невстраиваема по построению:")
        for r in noaddr:
            print(f"    · {r['finding'][:88]}")

    if suspects:
        print(f"\nКАНДИДАТЫ НА ПРОПУСК — {len(suspects)}")
        print("  След формулировки в указанном файле не найден. Перечитать:")
        for r in suspects:
            print(f"    · [{r.get('mode','—'):6}] {r['finding'][:70]}")
            print(f"      {r.get('why','')} · адрес: {r['where'][:60]}")
    else:
        print("\nКандидатов на пропуск нет.")

    if outside:
        print(f"\nВНЕ СКИЛЛА — {len(outside)}")
        print("  Адрес прямо говорит, что находка не про скилл: платформа,")
        print("  испытательный стенд, поведение модели. Решение принято и")
        print("  записано; к разбору не относится.")
        for r in outside:
            print(f"    · {r['finding'][:76]}")

    if vague:
        print(f"\nПРОВЕРЕНО ГРУБО — {len(vague)} из {len(rows)}")
        print("  В адресе не назван раздел, поэтому проверялось только наличие темы")
        print("  в файле. Вырезанное правило такой режим не поймает. Дописать раздел")
        print("  в адрес при следующем касании записи.")

    total = len(noaddr) + len(suspects)
    print(f"\nИТОГО к разбору: {total} из {len(rows)}")
    if total:
        print("Зелёный статус не доказывает, что находка учтена по существу — "
              "только что слова на месте. Красный требует человека.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
