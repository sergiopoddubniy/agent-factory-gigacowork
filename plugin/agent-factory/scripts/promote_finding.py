#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Переносит находку из проекта клиента в бэклог скилла.

    python3 promote_finding.py --проект "<папка клиента>" [--номер Н-3]
    python3 promote_finding.py --проект "<папка клиента>" --показать

**Зачем скрипт, если можно скопировать руками.** Можно — и полгода копировали.
Правило «переносить находки» без механизма истирается за две недели: находка
остаётся в папке клиента, у следующего клиента повторяется тот же дефект, и
никто не помнит, что его уже видели. Механизм дешевле дисциплины.

Что делает: читает `09_Контур_улучшения/НАХОДКИ_ДЛЯ_СКИЛЛА.md` проекта,
берёт находки со статусом «новая», дописывает их в `БЭКЛОГ.md` скилла
очередными номерами `С-<N>` — **со ссылкой на источник** — и проставляет в
проекте статус «перенесена».

Находка без графы «чем проверим» не переносится: в бэклоге скилла такая
строка — мнение, а мнению там не место.
"""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import re
import sys
import unicodedata

SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent


# Куда переносить: находка может относиться к любому скиллу конвейера.
# Один файл находок на проект и одно место переноса — иначе у консультанта
# появляются две привычки, и вторая не приживается.
SKILLS = {
    "gigacowork-agent-scenario": "gigacowork-agent-scenario",
    "personal-ai-research": "personal-ai-research",
}


def backlog_for(skill: str) -> pathlib.Path | None:
    """Бэклог названного скилла: свой — вверх по дереву, соседний — рядом."""
    if skill in ("", SKILLS["gigacowork-agent-scenario"]):
        return find_backlog()
    for d in [SKILL_ROOT, *SKILL_ROOT.parents][:5]:
        cand = d.parent / skill / "БЭКЛОГ.md"
        if cand.exists():
            return cand
    return None


def find_backlog() -> pathlib.Path | None:
    """Бэклог скилла ищется вверх по дереву, а не задаётся жёстким путём.

    Скилл живёт то в папке проекта, то распакованным из сборки, то в кэше
    плагинов — жёсткий путь ломается на первом же переносе и молча, потому
    что «файл не найден» легко принять за «переносить нечего».
    """
    for d in [SKILL_ROOT, *SKILL_ROOT.parents][:5]:
        cand = d / "БЭКЛОГ.md"
        if cand.exists():
            return cand
    return None

nfc = lambda s: unicodedata.normalize("NFC", s)


def resolve(raw: str):
    for cand in (raw, unicodedata.normalize("NFC", raw),
                 unicodedata.normalize("NFD", raw)):
        p = pathlib.Path(cand)
        if p.is_dir():
            return p
    return None


def parse(text: str):
    """Находки как блоки `### Н-<n>. <заголовок>` до следующего такого же."""
    out = []
    for m in re.finditer(r"^### (Н-\d+)\.\s*(.+?)\s*$", text, re.M):
        start = m.start()
        nxt = re.search(r"^### Н-\d+\.", text[m.end():], re.M)
        end = m.end() + (nxt.start() if nxt else len(text) - m.end())
        body = text[m.end():end]
        def field(pat: str) -> str:
            m2 = re.search(rf"\*\*(?:{pat}):\*\*\s*(.+?)\s*(?=\n\*\*|\Z)",
                           body, re.S)
            return m2.group(1) if m2 else ""
        # Метки принимаются в нескольких написаниях: человек пишет короче,
        # чем в шаблоне, и разбор, требующий точной формулировки, молча
        # объявит поле незаполненным — то есть проглотит находку.
        out.append(dict(
            key=m.group(1), title=m.group(2), span=(start, end),
            where=" ".join(field(r"Где вскрылось|Где нашли|Источник").split()),
            what=" ".join(field(r"Что произошло|Что случилось").split()),
            missing=" ".join(field(r"Чего не хватило[^:*]*").split()),
            how=" ".join(field(r"Чем проверим[^:*]*|Как проверим[^:*]*").split()),
            status=" ".join(field(r"Статус").split()).lower(),
            skill=" ".join(field(r"Скилл|Для скилла").split()).strip().lower(),
        ))
    return out


def next_key(backlog_text: str) -> tuple[str, int]:
    """Следующий номер В ТОЙ ЖЕ нумерации, что уже принята в бэклоге.

    У скиллов разные префиксы: «С-» у сборки агентов, «П-» у подготовки
    материалов. Скрипт, навязывающий свой префикс, создаёт в чужом бэклоге
    вторую нумерацию — и через месяц никто не скажет, С-4 и П-4 это одно
    и то же или разное.
    """
    found = re.findall(r"^### ([А-ЯЁA-Z])-(\d+)\.", backlog_text, re.M)
    if not found:
        return "С", 1
    prefix = max({p for p, _ in found}, key=lambda x: sum(1 for p, _ in found if p == x))
    nums = [int(n) for p, n in found if p == prefix]
    return prefix, max(nums) + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--проект", dest="proj", required=True)
    ap.add_argument("--номер", dest="only", default=None,
                    help="перенести одну находку, например Н-3")
    ap.add_argument("--показать", dest="dry", action="store_true",
                    help="ничего не менять, только показать, что будет перенесено")
    ap.add_argument("--скилл", dest="skill", default=None,
                    choices=sorted(SKILLS), help="в чей бэклог переносить")
    ap.add_argument("--бэклог", dest="backlog", default=None,
                    help="путь к БЭКЛОГ.md скилла, если он не найден автоматически")
    a = ap.parse_args()

    proj = resolve(a.proj)
    if proj is None:
        print(f"Папка проекта не найдена: {a.proj!r}")
        return 2
    src = proj / "09_Контур_улучшения/НАХОДКИ_ДЛЯ_СКИЛЛА.md"
    if not src.exists():
        print(f"Нет файла находок: {src}")
        return 2
    text = nfc(src.read_text(encoding="utf-8"))
    items = parse(text)
    fresh = [f for f in items
             if "перенесена" not in f["status"] and "закрыта" not in f["status"]]
    if a.only:
        fresh = [f for f in fresh if f["key"] == a.only]

    # Находка без поля «Скилл» относится к скиллу сборки агентов — так было
    # до появления второго скилла, и менять умолчание значило бы переписать
    # уже заведённые находки.
    target = (a.skill or "").strip().lower()
    if target:
        fresh = [f for f in fresh if (f["skill"] or SKILLS["gigacowork-agent-scenario"]) == target]
    else:
        by_skill = {}
        for f in fresh:
            by_skill.setdefault(f["skill"] or SKILLS["gigacowork-agent-scenario"], []).append(f)
        if len(by_skill) > 1:
            print("Находки относятся к разным скиллам — перенос по одному за раз:")
            for s, fs in sorted(by_skill.items()):
                print(f"  --скилл {s}   ({len(fs)}: {', '.join(x['key'] for x in fs)})")
            return 1
        target = next(iter(by_skill), SKILLS["gigacowork-agent-scenario"])

    backlog = pathlib.Path(a.backlog) if a.backlog else backlog_for(target)
    if backlog is None or not backlog.exists():
        print(f"Не найден БЭКЛОГ.md скилла «{target}». Укажите путь: --бэклог <файл>")
        return 2

    if not fresh:
        print("Новых находок нет.")
        return 0

    weak = [f for f in fresh if not f["how"]]
    fresh = [f for f in fresh if f["how"]]
    for f in weak:
        print(f"  ПРОПУЩЕНА · {f['key']} — не заполнено «чем проверим». "
              f"Без этого в бэклоге скилла остаётся мнение, а не задача.")

    if not fresh:
        return 1

    bl = backlog.read_text(encoding="utf-8")
    prefix, n = next_key(bl)
    today = dt.date.today().strftime("%d.%m.%Y")
    blocks, report = [], []
    for i, f in enumerate(fresh):
        key = f"{prefix}-{n + i}"
        blocks.append(
            f"""### {key}. {f['title']}

**Состояние:** Гипотеза. **Источник:** проект «{proj.name}», {f['where'] or 'дата не указана'} "
(перенесено {today}).

{f['what']}

**Чего не хватило в скилле:** {f['missing'] or 'не названо'}

**Чем проверим:** {f['how']}

---

""")
        report.append(f"  {f['key']} → {key}: {f['title'][:70]}")

    if a.dry:
        print(f"Будет перенесено в {backlog}:")
        print("\n".join(report))
        return 0

    if "## Закрытое" in bl:
        bl = bl.replace("## Закрытое", "".join(blocks) + "## Закрытое", 1)
    else:
        bl = bl.rstrip() + "\n\n" + "".join(blocks)
    backlog.write_text(bl, encoding="utf-8")

    # статус в проекте — чтобы находка не переносилась дважды
    for f in reversed(fresh):
        s, e = f["span"]
        block = text[s:e]
        block = re.sub(r"(\*\*Статус:\*\*\s*)(.+)",
                       rf"\g<1>перенесена в бэклог скилла {today}", block, count=1) \
            if "**Статус:**" in block else block.rstrip() + \
            f"\n**Статус:** перенесена в бэклог скилла {today}\n"
        text = text[:s] + block + text[e:]
    src.write_text(text, encoding="utf-8")

    print(f"Перенесено находок: {len(fresh)}")
    print("\n".join(report))
    print(f"\nБэклог скилла: {backlog}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
