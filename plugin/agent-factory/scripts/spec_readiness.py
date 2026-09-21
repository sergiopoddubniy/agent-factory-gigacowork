#!/usr/bin/env python3
"""
spec_readiness.py — ворота достаточности спецификации агента.

Два порога, а не один:

  demo   — достаточно, чтобы собрать и показать агента. Допущения
           разрешены и обязаны быть названы; обязательны периметр, входы и
           выходы, триггер, правила хотя бы допущением, пять обязательных
           приёмочных случаев.
  pilot  — достаточно, чтобы измерять эффект на реальных задачах: ни одного
           отсутствующего блока, гипотеза результата с бейзлайном не
           допущением, HITL-карта с владельцем, эталонные разборы (оракул),
           владелец процесса.

Глубина зависит от класса применимости: для детерминированной задачи
PR/FAQ и решение об адаптации не требуются — это защита от полного
Discovery на мелочи.

Код возврата зависит от результата: 0 — порог пройден, 1 — нет,
2 — ошибка входа. Отчёт печатается всегда.

    python3 spec_readiness.py --spec AGENT_SPEC.md [--threshold demo|pilot] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factory_common import (  # noqa: E402
    BLOCKS, EXIT_INPUT, EXIT_OK, EXIT_RED, HYPOTHESIS_FIELDS, REQUIRED_CASES,
    STATUS_ASSUMED, STATUS_FILLED, STATUS_MISSING, Spec, case_ids, ears_lines,
    hitl_rows, io_lists, nfc, parse_spec, rules, scope_lists,
)

# Блоки, которые для детерминированного класса не требуются ни на одном
# пороге (сжатый Discovery).
SKIP_FOR_DETERMINISTIC = {2, 4}


def _f(code: str, block: int, msg: str, fix: str) -> dict:
    return {"code": code, "block": block, "message": msg, "fix": fix}


def check(spec: Spec, threshold: str) -> dict:
    """Возвращает словарь с вердиктом, находками и статусами блоков."""
    findings = []
    for p in spec.problems:
        findings.append(_f("Ф-00-РАЗБОР", 0, p, "исправь структуру файла спецификации"))

    klass = spec.klass
    skip = SKIP_FOR_DETERMINISTIC if klass == "детерминированный" else set()
    pilot = threshold == "pilot"

    # --- Общее правило порогов по статусам блоков -------------------------
    demo_must_fill = {1, 5, 6, 7, 12}
    demo_may_assume = {3, 8, 9, 10, 11, 13, 14, 15} | ({2, 4} - skip)
    for n, title in BLOCKS.items():
        if n in skip:
            continue
        b = spec.block(n)
        if pilot:
            if b.status == STATUS_MISSING:
                findings.append(_f("Ф-%02d-ОТСУТСТВУЕТ" % n, n,
                                   f"блок {n} «{title}» пуст",
                                   "для пилота пустых блоков быть не может"))
        else:
            if n in demo_must_fill and b.status != STATUS_FILLED:
                findings.append(_f("Ф-%02d-ОТСУТСТВУЕТ" % n, n,
                                   f"блок {n} «{title}»: {b.status}",
                                   "для демо этот блок нужен заполненным, а не допущением"))
            elif n in demo_may_assume and b.status == STATUS_MISSING:
                findings.append(_f("Ф-%02d-МОЛЧАНИЕ" % n, n,
                                   f"блок {n} «{title}» пуст",
                                   "допущение разрешено, молчание — нет: напиши "
                                   "«Допущение: …» или «Не знаем: …»"))

    # --- Блок 3: гипотеза результата --------------------------------------
    b3 = spec.block(3)
    if b3.status != STATUS_MISSING:
        missing = [f for f in HYPOTHESIS_FIELDS if not b3.field_value(f)]
        if missing and (pilot or b3.status == STATUS_FILLED):
            findings.append(_f("Ф-03-ПОЛЯ", 3,
                               "гипотеза результата без полей: " + ", ".join(missing),
                               "шесть полей обязательны: " + ", ".join(HYPOTHESIS_FIELDS)))
        base = (b3.field_value("Бейзлайн") or "").lower()
        if pilot and (b3.status == STATUS_ASSUMED or not base or "допущ" in base or "?" in base):
            findings.append(_f("Ф-03-БЕЙЗЛАЙН", 3,
                               "бейзлайн — допущение или не измерен",
                               "для пилота бейзлайн измеряется (хронометраж, журнал), "
                               "а не оценивается"))
        metric = (b3.field_value("Метрика") or "").lower()
        if metric and any(w in metric for w in ("улучшить", "повысить эффективность", "оптимизировать")) \
                and not any(ch.isdigit() for ch in metric):
            findings.append(_f("Ф-03-НЕИЗМЕРИМО", 3,
                               f"метрика звучит как намерение: «{metric}»",
                               "метрика — что измеряется и в каких единицах, "
                               "а не «улучшить»"))

    # --- Блок 4: решение об адаптации -------------------------------------
    b4 = spec.block(4)
    if 4 not in skip and b4.status != STATUS_MISSING:
        decision = (b4.field_value("Решение") or "").lower()
        if not decision or not any(w in decision for w in ("адаптац", "перепроект")):
            findings.append(_f("Ф-04-РЕШЕНИЕ", 4,
                               "решение не названо словом «адаптация» или «перепроектирование»",
                               "строка «Решение: адаптация | перепроектирование» и «Обоснование: …»"))

    # --- Блок 5: пользователь и триггер -----------------------------------
    b5 = spec.block(5)
    if b5.status != STATUS_MISSING:
        for fld in ("Роль", "Триггер"):
            if not b5.field_value(fld):
                findings.append(_f("Ф-05-ПОЛЯ", 5, f"нет строки «{fld}:»",
                                   "роль пользователя и что запускает работу агента"))

    # --- Блок 6: входы и выходы -------------------------------------------
    b6 = spec.block(6)
    if b6.status != STATUS_MISSING:
        ins, outs = io_lists(b6)
        if not ins:
            findings.append(_f("Ф-06-ВХОДЫ", 6, "нет списка «Входы:»",
                               "перечисли входные документы/данные, по одному в строке"))
        if not outs:
            findings.append(_f("Ф-06-ВЫХОДЫ", 6, "нет списка «Выходы:»",
                               "перечисли результаты агента, по одному в строке"))

    # --- Блок 7: периметр -------------------------------------------------
    b7 = spec.block(7)
    if b7.status != STATUS_MISSING:
        inside, outside = scope_lists(b7)
        if not inside:
            findings.append(_f("Ф-07-INSCOPE", 7, "нет списка «In-scope:»",
                               "что агент делает — одной фразой на пункт"))
        if not outside:
            findings.append(_f("Ф-07-OUTSCOPE", 7, "нет списка «Out-of-scope:»",
                               "два-три соседних запроса, которые агент НЕ берёт"))

    # --- Блок 8: правила --------------------------------------------------
    b8 = spec.block(8)
    if pilot and b8.status != STATUS_MISSING and len(rules(b8)) < 3:
        findings.append(_f("Ф-08-МАЛО", 8, f"правил решений: {len(rules(b8))}",
                           "для пилота нужен реестр решений: признак, порог, что "
                           "делать при неоднозначности — не меньше трёх"))

    # --- Блок 9: неопределённость (EARS) ----------------------------------
    b9 = spec.block(9)
    if b9.status != STATUS_MISSING:
        n_ears = len(ears_lines(b9))
        need = 3 if pilot else 1
        if n_ears < need:
            findings.append(_f("Ф-09-EARS", 9, f"строк вида «Когда …, агент должен …»: {n_ears}",
                               f"нужно не меньше {need}: так поведение читается и "
                               "человеком, и проверкой"))

    # --- Блок 10: HITL-карта ----------------------------------------------
    b10 = spec.block(10)
    if b10.status != STATUS_MISSING:
        rows = hitl_rows(b10)
        if not rows and (pilot or b10.status == STATUS_FILLED):
            findings.append(_f("Ф-10-ТАБЛИЦА", 10, "нет строк таблицы HITL-карты",
                               "таблица: Точка | Кто | Критерий | Что блокирует | Эскалация"))
        if pilot and rows and any(("?" in r[1]) or not r[1].strip() for r in rows):
            findings.append(_f("Ф-10-КТО", 10, "в HITL-карте не назван валидатор",
                               "колонка «Кто» — роль, а не знак вопроса"))

    # --- Блок 12: приёмочные случаи ---------------------------------------
    b12 = spec.block(12)
    if b12.status != STATUS_MISSING:
        ids = case_ids(b12)
        missing = [c for c in REQUIRED_CASES if c not in ids]
        if missing:
            findings.append(_f("Ф-12-ОБЯЗАТЕЛЬНЫЕ", 12,
                               "нет обязательных случаев: " + ", ".join(missing),
                               "пять обязательных id: " + ", ".join(REQUIRED_CASES)))
        domain = [c for c in ids if c not in REQUIRED_CASES]
        if pilot and len(domain) < 2:
            findings.append(_f("Ф-12-ПРЕДМЕТНЫЕ", 12, f"предметных случаев: {len(domain)}",
                               "для пилота нужны не меньше двух случаев из практики "
                               "владельца процесса"))

    # --- Блок 13: эталонные разборы (оракул) ------------------------------
    b13 = spec.block(13)
    if pilot:
        items = [x for x in b13.bullets()
                 if x and x.lower() not in ("нет", "—")
                 and not re.match(r"^(?:\*\*)?(Допущение|Не знаем)(?:\*\*)?\s*:", x, re.I)]
        if b13.status != STATUS_FILLED or len(items) < 2:
            findings.append(_f("Ф-13-ОРАКУЛ", 13,
                               f"эталонных разборов: {len(items)}",
                               "без двух-трёх разборов, сделанных экспертом, у пилота "
                               "нет способа отличить улучшение агента от его видимости"))

    # --- Блок 15: владелец ------------------------------------------------
    b15 = spec.block(15)
    if pilot and b15.status != STATUS_MISSING and not b15.field_value("Владелец процесса"):
        findings.append(_f("Ф-15-ВЛАДЕЛЕЦ", 15, "не назван владелец процесса",
                           "строка «Владелец процесса: <роль>» — он подписывает приёмку"))

    statuses = {n: spec.block(n).status for n in BLOCKS}
    unknowns = {n: spec.block(n).unknowns for n in BLOCKS if spec.block(n).unknowns}
    assumptions = {n: spec.block(n).assumptions for n in BLOCKS if spec.block(n).assumptions}
    return {
        "spec": str(spec.path),
        "id": spec.slug,
        "fingerprint": spec.fp,
        "threshold": threshold,
        "class": klass,
        "verdict": "PASS" if not findings else "FAIL",
        "findings": findings,
        "statuses": statuses,
        "assumptions": assumptions,
        "unknowns": unknowns,
    }


def render(report: dict) -> str:
    out = []
    out.append(f"ВОРОТА ДОСТАТОЧНОСТИ · порог {report['threshold']} · "
               f"{report['id'] or '<без id>'} · отпечаток {report['fingerprint']}")
    out.append(f"вердикт: {report['verdict']}")
    out.append("")
    out.append("Блоки:")
    for n, title in BLOCKS.items():
        st = report["statuses"][n]
        mark = {"заполнено": "●", "допущение": "◐", "отсутствует": "○"}[st]
        out.append(f"  {mark} {n:2d}. {title} — {st}")
    if report["findings"]:
        out.append("")
        out.append("Находки:")
        for f in report["findings"]:
            out.append(f"  - [{f['code']}] {f['message']}")
            out.append(f"      → {f['fix']}")
    if report["assumptions"]:
        out.append("")
        out.append("Допущения (уходят в handoff списком):")
        for n, items in report["assumptions"].items():
            for a in items:
                out.append(f"  - блок {n}: {a}")
    if report["unknowns"]:
        out.append("")
        out.append("Явные пробелы «Не знаем» (вход для плана добора):")
        for n, items in report["unknowns"].items():
            for u in items:
                out.append(f"  - блок {n}: {u}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Ворота достаточности спецификации агента")
    ap.add_argument("--spec", required=True, help="путь к AGENT_SPEC.md")
    ap.add_argument("--threshold", choices=["demo", "pilot"], default="demo",
                    help="порог: demo (по умолчанию) или pilot")
    ap.add_argument("--json", action="store_true", help="печатать JSON вместо отчёта")
    args = ap.parse_args(argv)

    spec = parse_spec(Path(args.spec))
    report = check(spec, args.threshold)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return EXIT_OK if report["verdict"] == "PASS" else EXIT_RED


if __name__ == "__main__":
    sys.exit(main())
