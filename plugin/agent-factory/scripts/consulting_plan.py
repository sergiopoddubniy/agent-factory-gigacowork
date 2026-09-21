#!/usr/bin/env python3
"""
consulting_plan.py — план добора информации, выведенный из дыр спецификации.

План не пишется с чистого листа: каждая находка ворот (spec_readiness)
превращается в одно действие с клиентом — кого спросить, каким методом,
сколько времени это займёт у людей клиента, что именно хотим узнать и
каким артефактом закончится. Одинаковые действия склеиваются.

Глубина плана задаётся классом применимости: для детерминированной задачи
PR/FAQ и решение об адаптации не запрашиваются; для ИИ-помощника сессии
сжатые (30 минут вместо 60–90).

Выход — markdown на одну страницу (stdout или --out). Код возврата:
0 — план построен (в том числе пустой: «добор не нужен»), 2 — ошибка входа.

    python3 consulting_plan.py --spec AGENT_SPEC.md --threshold pilot [--out ПЛАН_ДОБОРА.md]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factory_common import EXIT_OK, parse_spec  # noqa: E402
from spec_readiness import check  # noqa: E402

# Блок → действие. Время — время людей клиента, не консультанта.
ACTIVITIES = {
    1: dict(what="зачем нужен агент, что меняется и кто заинтересован",
            who="заказчик изменения (руководитель подразделения)",
            method="разбор перехода (switch interview)", hours=1.0,
            result="блок 1 «Возможность» заполнен; одна фраза о результате, а не о функции"),
    2: dict(what="как выглядит успешный запуск глазами первых пользователей",
            who="заказчик изменения + 2 будущих пользователя",
            method="сессия PR/FAQ (Working Backwards)", hours=1.5,
            result="полстраницы «пресс-релиза из будущего» и 3–5 вопросов первых пользователей"),
    3: dict(what="какая метрика изменится, какой у неё бейзлайн, за какой срок и что считается провалом",
            who="владелец процесса + аналитик эффекта (не из команды разработки)",
            method="хронометраж 6–10 задач или выгрузка журнала за прошлый период", hours=3.0,
            result="шесть полей гипотезы результата; бейзлайн — измерен, не оценён"),
    4: dict(what="автоматизируем существующий процесс или перестраиваем его вокруг агента",
            who="заказчик изменения + владелец процесса",
            method="сессия решения адаптация/перепроектирование", hours=1.0,
            result="строка «Решение:» с обоснованием; названо, какие шаги исчезают"),
    5: dict(what="кто именно работает с агентом и что запускает работу",
            who="исполнитель роли (2 человека на роль)",
            method="наблюдение за работой (contextual inquiry)", hours=2.0,
            result="роль, триггер и реальная, а не нормативная, последовательность шагов"),
    6: dict(what="какие документы и данные приходят на вход и что должно получиться на выходе",
            who="исполнитель роли",
            method="наблюдение за работой с реальными файлами", hours=1.5,
            result="списки «Входы» и «Выходы» с примерами файлов"),
    7: dict(what="где граница агента: что он делает и что соседнее — не его",
            who="владелец процесса",
            method="разбор трёх соседних запросов, которые легко перепутать", hours=0.5,
            result="In-scope и Out-of-scope списками"),
    8: dict(what="по каким признакам и порогам принимаются решения и что делать при неоднозначности",
            who="тот, кто принимает решения в процессе (2 человека)",
            method="разбор конкретного случая (critical decision method)", hours=3.0,
            result="реестр решений: признак → порог → действие при неоднозначности"),
    9: dict(what="как агент должен вести себя, когда данных нет, они противоречат или запрос вне периметра",
            who="владелец процесса",
            method="разбор пяти неудобных случаев", hours=1.0,
            result="не меньше трёх строк «Когда …, агент должен …»"),
    10: dict(what="кто и на каком шаге подтверждает результат агента, что без подтверждения запрещено",
             who="владелец процесса + руководитель",
             method="согласование HITL-карты", hours=0.5,
             result="таблица HITL-карты с ролями и эскалацией"),
    11: dict(what="к каким системам агент имеет доступ, что запрещено писать и хранить",
             who="владелец систем + ИБ",
             method="согласование уровня доступа и ограничений", hours=0.5,
             result="уровень доступа L0–L3, список запретов"),
    12: dict(what="на каких случаях из практики принимается работа агента",
             who="владелец процесса",
             method="формулировка приёмочных случаев по формуле «вход → ожидание»", hours=1.0,
             result="пять обязательных и не меньше двух предметных случаев"),
    13: dict(what="как выглядит правильный результат на реальных документах — оракул для проверки",
             who="эксперт процесса",
             method="эталонные разборы 2–3 документов, заполненные самим экспертом", hours=3.0,
             result="2–3 эталонных разбора в комплекте; без них пилот не отличает улучшение от видимости"),
    14: dict(what="был ли похожий агент и что из него можно взять",
             who="команда фабрики (без клиента)",
             method="проверка реестра агентов и базы шаблонов", hours=0.0,
             result="запись: что рассмотрено, что взято, что отвергнуто"),
    15: dict(what="кто владеет процессом и подписывает приёмку",
             who="руководитель подразделения",
             method="одно письмо", hours=0.25,
             result="владелец процесса и дата приёмки названы"),
}

COMPRESSED = {"ии-помощник": 0.5, "детерминированный": 0.5}


def build_plan(spec, threshold: str) -> dict:
    report = check(spec, threshold)
    blocks_needed = sorted({f["block"] for f in report["findings"] if f["block"] in ACTIVITIES})
    factor = COMPRESSED.get(report["class"], 1.0)
    items = []
    for n in blocks_needed:
        a = dict(ACTIVITIES[n])
        a["block"] = n
        a["hours"] = round(a["hours"] * (factor if n in (1, 2, 4, 7, 9) else 1.0), 2)
        reasons = [f["message"] for f in report["findings"] if f["block"] == n]
        a["reasons"] = reasons
        items.append(a)
    total = round(sum(i["hours"] for i in items), 2)
    open_q = [(n, u) for n, us in report["unknowns"].items() for u in us]
    return {"report": report, "items": items, "total_hours": total, "open_questions": open_q}


def render(plan: dict, spec) -> str:
    r = plan["report"]
    name = spec.fm("имя агента") or r["id"]
    out = [f"# План добора информации — {name}", "",
           f"Спецификация: `{r['id']}` · отпечаток {r['fingerprint']} · порог **{r['threshold']}** · "
           f"класс применимости: {r['class'] or 'не задан'} · дата: {date.today().isoformat()}", ""]
    if not plan["items"]:
        out.append("**Добор не нужен:** ворота порога пройдены. Спецификация готова к сборке "
                   "(demo) или к измерению (pilot).")
        return "\n".join(out) + "\n"
    out.append(f"Ворота порога **{r['threshold']}** не пройдены: {len(r['findings'])} находок, "
               f"{len(plan['items'])} действий. Время людей клиента — **{plan['total_hours']} ч**. "
               "Согласуется с клиентом до начала, одной страницей.")
    out.append("")
    out.append("| # | Что хотим узнать | Кого | Метод | Время клиента, ч | Чем закончится |")
    out.append("|---|---|---|---|---|---|")
    for i, a in enumerate(plan["items"], 1):
        out.append(f"| {i} | {a['what']} | {a['who']} | {a['method']} | {a['hours']:g} | {a['result']} |")
    out.append("")
    out.append("## Почему каждое действие в плане")
    for i, a in enumerate(plan["items"], 1):
        out.append(f"{i}. Блок {a['block']} «{spec.block(a['block']).title}»: " + "; ".join(a["reasons"]))
    if any(a["block"] == 13 for a in plan["items"]):
        out.append("")
        out.append("**Эталонные разборы — отдельной строкой.** Это самая труднодоговариваемая "
                   "часть плана: она требует времени эксперта. Просить на согласовании плана, "
                   "а не когда агент уже собран и его надо проверять. Отказ клиента — не повод "
                   "не делать агента, а повод записать в комплект: «у пилота нет оракула».")
    if plan["open_questions"]:
        out.append("")
        out.append("## Открытые вопросы из спецификации («Не знаем»)")
        for n, q in plan["open_questions"]:
            out.append(f"- блок {n}: {q}")
    if r["assumptions"]:
        out.append("")
        out.append("## Допущения, с которыми можно собирать демо уже сейчас")
        for n, items in r["assumptions"].items():
            for a in items:
                out.append(f"- блок {n}: {a}")
    out.append("")
    out.append("Порядок: наблюдение → разбор случая → эталонные разборы → гипотеза результата → "
               "HITL и владелец. Двух человек на роль достаточно, чтобы поймать расхождение в "
               "практике; одного — нет.")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="План добора информации из дыр спецификации")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--threshold", choices=["demo", "pilot"], default="pilot")
    ap.add_argument("--out", help="куда записать план (по умолчанию — stdout)")
    args = ap.parse_args(argv)
    spec = parse_spec(Path(args.spec))
    plan = build_plan(spec, args.threshold)
    text = render(plan, spec)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"план записан: {args.out} · действий: {len(plan['items'])} · "
              f"часов клиента: {plan['total_hours']:g}")
    else:
        print(text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
