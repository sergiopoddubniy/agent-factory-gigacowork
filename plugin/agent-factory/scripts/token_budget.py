#!/usr/bin/env python3
"""
token_budget.py — сколько контекста съедает сам скилл.

Зачем. Скилл — это контекст, и он подчиняется тому же правилу, что и всё
остальное: длинный контекст используется неравномерно, часть правил перестаёт
срабатывать. Файл `output_stability.md`, уровень 9, говорит об этом про агента;
к самому скиллу это относится ровно так же и обнаруживается только замером.

Пороги взяты из спецификации Agent Skills и практики её валидаторов:

| Что | Предупреждение | Ошибка |
|---|---|---|
| SKILL.md | 5 000 токенов или 500 строк | — |
| один файл references/ | 10 000 | 25 000 |
| все references/ вместе | 25 000 | 50 000 |

Оговорка про точность и почему она чаще всего не важна. Токенизатор
`o200k_base` везёт словарь не в пакете, а скачивает его при первом вызове; без
сети число оценивается по длине текста — для русской прозы приходится примерно
один токен на 2,6–3,2 знака.

Но решение зависит не от числа, а от того, по какую сторону порога оно лежит.
Поэтому скрипт печатает **не только оценку, но и устойчивость вывода**: если
даже самая щадящая граница диапазона выше порога, вердикт от точности словаря
не зависит и точный замер ничего не изменит. Пересчитывать имеет смысл только
там, где порог попал внутрь диапазона.

На машине с сетью и установленным `tiktoken` скрипт сам возьмёт точный
токенизатор — никаких флагов не нужно.

Запуск:  python3 scripts/token_budget.py [папка скилла]
Код возврата: 0 — в пределах, 1 — превышение уровня «ошибка», 2 — предупреждения.
"""
from __future__ import annotations

import sys
from pathlib import Path

LO, MID, HI = 3.2, 2.8, 2.6          # знаков на токен: нижняя, рабочая, верхняя
W_SKILL_TOK, W_SKILL_LINES = 5000, 500
W_REF, E_REF = 10000, 25000
W_ALL, E_ALL = 25000, 50000

try:
    import tiktoken
    _enc = tiktoken.get_encoding("o200k_base")
except Exception:
    _enc = None


def verdict(lo: int, hi: int, threshold: int, exact: bool) -> str:
    """Насколько вывод зависит от точности подсчёта.

    Смысл: решение принимается не по числу, а по стороне порога. Если весь
    диапазон оценки лежит по одну сторону, точный токенизатор ничего не
    изменит, и «оценочность» числа перестаёт быть оговоркой.
    """
    if exact:
        return "замер точный"
    if lo > threshold:
        return "вывод устойчив: порог превышен даже по самой щадящей границе оценки"
    if hi < threshold:
        return "вывод устойчив: порог не достигнут даже по самой строгой границе"
    return ("вывод НЕ устойчив: порог попал внутрь диапазона — "
            "нужен точный замер с токенизатором")


def count(text: str) -> tuple[int, int, int]:
    if _enc is not None:
        n = len(_enc.encode(text))
        return n, n, n
    return round(len(text) / LO), round(len(text) / MID), round(len(text) / HI)


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
    sk = root / "SKILL.md"
    if not sk.exists():
        print(f"нет SKILL.md в {root}")
        return 3
    exact = _enc is not None
    print("=" * 64)
    print("БЮДЖЕТ КОНТЕКСТА СКИЛЛА" + ("" if exact else "  (оценка: токенизатор недоступен)"))
    print("=" * 64)

    t = sk.read_text(encoding="utf-8")
    body = t.split("---", 2)[2] if t.startswith("---") else t
    lo, mid, hi = count(body)
    lines = len(body.splitlines())
    warn = err = 0
    over_tok, over_lines = lo > W_SKILL_TOK, lines > W_SKILL_LINES
    warn += over_tok or over_lines
    print(f"\nSKILL.md — читается всегда, при каждом вызове")
    print(f"  {mid:,} токенов" + ("" if exact else f" ({lo:,}–{hi:,})") + f", {lines} строк")
    print(f"  порог: {W_SKILL_TOK:,} токенов или {W_SKILL_LINES} строк"
          f"  →  {'ПРЕВЫШЕН' if over_tok or over_lines else 'в норме'}")
    if over_tok:
        print(f"  превышение по токенам минимум в {lo / W_SKILL_TOK:.1f} раза")
    print("  " + verdict(lo, hi, W_SKILL_TOK, exact))

    refs = sorted((root / "references").glob("*.md"))
    tl = tm = th = 0
    flagged = []
    for p in refs:
        a, b, c = count(p.read_text(encoding="utf-8"))
        tl += a; tm += b; th += c
        if a > E_REF:
            flagged.append((p.name, b, "ОШИБКА")); err += 1
        elif a > W_REF:
            flagged.append((p.name, b, "предупреждение")); warn += 1
    print(f"\nreferences/ — читаются по мере надобности, не все сразу")
    print(f"  файлов: {len(refs)}, суммарно {tm:,} токенов"
          + ("" if exact else f" ({tl:,}–{th:,})"))
    print(f"  пороги суммы: предупреждение {W_ALL:,}, ошибка {E_ALL:,}"
          f"  →  {'ОШИБКА' if tl > E_ALL else ('ПРЕВЫШЕН' if tl > W_ALL else 'в норме')}")
    print("  " + verdict(tl, th, E_ALL, exact))
    if tl > E_ALL:
        err += 1
    elif tl > W_ALL:
        warn += 1
    for name, n, level in flagged:
        print(f"    {level}: {name} — {n:,}")

    print("\nЧто с этим делать, если превышено:")
    print("  · SKILL.md держит только пайплайн и правила, применяемые всегда;")
    print("    подробности переезжают в references/ и читаются по ссылке;")
    print("  · один справочник крупнее 25 000 токенов делится по темам —")
    print("    иначе он загружается целиком ради одного абзаца;")
    print("  · раздел, не названный ни в одном шаге пайплайна, удаляется:")
    print("    он занимает место и не срабатывает.")

    print(f"\nИТОГО: ошибок {err}, предупреждений {warn}")
    if not exact:
        print("Числа оценочные: токенизатор скачивает словарь из сети, её здесь нет.")
        print("Где выше сказано «вывод устойчив» — точный замер результата не изменит.")
    return 1 if err else (2 if warn else 0)


if __name__ == "__main__":
    sys.exit(main())
