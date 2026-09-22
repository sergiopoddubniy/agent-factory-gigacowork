"""Движок консолидации — арифметика и адресация, правила из rules/*.yaml.

Контракт обратной связи (agent_package.md, «Живая проверка»): каждая
проверка возвращает объект `id, severity, message, fix_hint, where,
expected, actual, delta`. При BLOCKING результат физически не создаётся —
`run.py` не пишет файл формы, если хоть одна находка BLOCKING.

Три уровня строгости:
  BLOCKING — результат не выдаётся (нарушение регламента §4.3/§4.4/§4.6,
             неопознанная или отсутствующая компания периметра, ОСВ/Ф1/Ф2
             компании не сходятся сами по себе);
  WARNING  — выдаётся с пометкой (применена корректировка §4.2 — это
             штатное, но видимое отклонение от «сырых» данных);
  INFO     — наблюдение (контроль сошёлся; используется в протоколе, чтобы
             в нём был явный OK по каждому пункту регламента §5, а не
             только список проблем).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .io_xlsx import ОСВ, СтрокаВГО
from .normalize import Периметр, name_key

BLOCKING, WARNING, INFO = "BLOCKING", "WARNING", "INFO"


def _f(id_, severity, message, fix_hint, where="", expected=None, actual=None, delta=None):
    return {"id": id_, "severity": severity, "message": message, "fix_hint": fix_hint,
            "where": where, "expected": expected, "actual": actual, "delta": delta}


@dataclass
class Result:
    findings: list = field(default_factory=list)
    company_f1: dict = field(default_factory=dict)   # code -> {comp_code: value}
    company_f2: dict = field(default_factory=dict)
    g_f1: dict = field(default_factory=dict)
    h_f1: dict = field(default_factory=dict)
    g_f2: dict = field(default_factory=dict)
    h_f2: dict = field(default_factory=dict)
    journal: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    vgo_lines_report: list = field(default_factory=list)  # для стоп-отчёта §4.4

    @property
    def blocking(self) -> bool:
        return any(f["severity"] == BLOCKING for f in self.findings + self.checks)

    def add(self, finding):
        self.findings.append(finding)


def _statute_value(osv: ОСВ, слагаемые: list) -> int:
    total = 0
    for term in слагаемые:
        acc = osv.счета.get(term["счёт"])
        if acc is None:
            continue
        total += term["знак"] * acc[term["source"]]
    return int(round(total))


def match_companies(osvs: list[ОСВ], rules: dict, result: Result) -> dict:
    """{код_периметра: ОСВ}. Пишет BLOCKING в result при любом расхождении
    с периметром регламента §1 — не выбирает похожую компанию сама."""
    периметр = Периметр.из_правил(rules)
    орг_формы = rules.get("орг_формы", [])
    by_code, неопознанные = {}, []
    for osv in osvs:
        код = периметр.опознать(osv.организация_сырая, орг_формы)
        if код is None:
            неопознанные.append(osv.организация_сырая)
            continue
        if код in by_code:
            result.add(_f("П-ПЕРИМЕТР-ДУБЛЬ", BLOCKING,
                           f"компания «{osv.организация_сырая}» и «{by_code[код].организация_сырая}» "
                           f"дают один и тот же код периметра {код}",
                           "проверить, не загружена ли одна ОСВ дважды под разными файлами",
                           where=osv.путь))
            continue
        by_code[код] = osv
    if неопознанные:
        result.add(_f("П-ПЕРИМЕТР-ЧУЖАЯ", BLOCKING,
                       "в загруженных ОСВ есть компания, которой нет в периметре регламента §1: "
                       + "; ".join(неопознанные),
                       "по регламенту §1 любая компания вне трёх названных — ошибка источника, "
                       "а не расширение периметра; проверить название и состав загруженных файлов",
                       where="; ".join(o.путь for o in osvs)))
    отсутствуют = [c for c in периметр.коды() if c not in by_code]
    if отсутствуют:
        имена = [периметр.каноническое(c) for c in отсутствуют]
        result.add(_f("П-ПЕРИМЕТР-НЕХВАТКА", BLOCKING,
                       "не хватает ОСВ по компании(ям) периметра: " + ", ".join(имена),
                       "загрузить недостающую ОСВ за этот квартал; без неё консолидация не выполняется",
                       where="; ".join(o.путь for o in osvs)))
    return by_code


def compute_company_lines(by_code: dict, rules: dict, result: Result) -> tuple[dict, dict]:
    """Базовые (до корректировок и элиминаций) значения статей Ф1/Ф2 по
    каждой компании — регламент §3, соответствие статьи счетам ОСВ."""
    f1, f2 = {}, {}
    for stat in rules["статьи_ф1"]:
        f1[stat["код"]] = {code: _statute_value(osv, stat["слагаемые"])
                            for code, osv in by_code.items()} if stat["слагаемые"] else {}
    for stat in rules["статьи_ф2"]:
        f2[stat["код"]] = {code: _statute_value(osv, stat["слагаемые"])
                            for code, osv in by_code.items()} if stat["слагаемые"] else {}

    # К1 — ОСВ каждой компании: сальдо Дт = сальдо Кт на конец периода.
    периметр = Периметр.из_правил(rules)
    for code, osv in by_code.items():
        exp, act = osv.итого["конец_дт"], osv.итого["конец_кт"]
        ok = int(exp) == int(act)
        result.checks.append(_f("К1-ОСВ-БАЛАНС", INFO if ok else BLOCKING,
                                 f"ОСВ «{периметр.каноническое(code)}»: сальдо Дт/Кт на конец периода",
                                 "сверить выгрузку ОСВ из 1С — файл внутренне не сбалансирован",
                                 where=osv.путь, expected=exp, actual=act, delta=act - exp))
    return f1, f2


def process_vgo(rows: list[СтрокаВГО], by_code: dict, rules: dict, result: Result) -> dict:
    """Возвращает {(комп_а, комп_б): {"Расчёты": δ, "Обороты": δ}} только по
    парам, прошедшим опознание и проверку §4.3; попутно пишет корректировки
    (§4.2), элиминации (§4.5) и находки (§4.3/§4.4) в result."""
    периметр = Периметр.из_правил(rules)
    орг_формы = rules.get("орг_формы", [])
    порог = rules["вго"]["порог_блокирует"] and rules["порог_существенности_вго"]

    by_pair_type: dict = {}
    for row in rows:
        код_а = периметр.опознать(row.компания_а_сырая, орг_формы)
        код_б = периметр.опознать(row.компания_б_сырая, орг_формы)
        неопознано = [s for s, к in ((row.компания_а_сырая, код_а), (row.компания_б_сырая, код_б)) if к is None]
        if неопознано:
            result.add(_f("П-ВГО-ЧУЖАЯ", BLOCKING,
                           f"реестр ВГО, строка {row.номер}: компания не входит в периметр регламента §1: "
                           + "; ".join(неопознано),
                           "проверить написание названия компании в реестре ВГО или состав периметра",
                           where=f"{row.путь}!строка {row.строка_листа}"))
            continue
        if row.тип not in rules["вго"]["типы"]:
            result.add(_f("П-ВГО-ТИП", BLOCKING,
                           f"реестр ВГО, строка {row.номер}: неизвестный тип операции «{row.тип}»",
                           "тип строки реестра — «Расчёты» или «Обороты» (регламент §2, п.2)",
                           where=f"{row.путь}!строка {row.строка_листа}"))
            continue
        δ = row.сумма_а - row.сумма_б
        by_pair_type.setdefault((код_а, код_б), {})[row.тип] = {
            "δ": δ, "а": row.сумма_а, "б": row.сумма_б, "row": row}

    pairs_ok: dict = {}
    for (код_а, код_б), by_type in by_pair_type.items():
        типы = rules["вго"]["типы"]
        отсутствуют_типы = [t for t in типы if t not in by_type]
        if отсутствуют_типы:
            result.add(_f("П-ВГО-НЕПОЛНАЯ-ПАРА", BLOCKING,
                           f"пара «{периметр.каноническое(код_а)}» → «{периметр.каноническое(код_б)}»: "
                           f"нет строки типа {', '.join(отсутствуют_типы)} — сверить «Расчёты» с "
                           "«Оборотами» (регламент §4.3) невозможно",
                           "добавить недостающую строку реестра ВГО для этой пары за квартал",
                           where=""))
            continue
        δ_р = by_type["Расчёты"]["δ"]
        δ_о = by_type["Обороты"]["δ"]
        if round(δ_р, 2) != round(δ_о, 2):
            r1, r2 = by_type["Расчёты"]["row"], by_type["Обороты"]["row"]
            result.add(_f("П-ВГО-4.3-НЕСОГЛАСОВАНО", BLOCKING,
                           f"пара «{периметр.каноническое(код_а)}» → «{периметр.каноническое(код_б)}»: "
                           f"расхождение по «Расчётам» ({δ_р:,.0f} ₽) не совпадает с расхождением по "
                           f"«Оборотам» ({δ_о:,.0f} ₽) — регламент §4.3, причина не установлена",
                           "консолидация останавливается до разбора отделом консолидации (регламент §4.3); "
                           "агент/харнес расхождение не разрешает",
                           where=f"{r1.путь}!строка {r1.строка_листа}, {r2.путь}!строка {r2.строка_листа}",
                           expected=δ_о, actual=δ_р, delta=δ_р - δ_о))
            continue
        pairs_ok[(код_а, код_б)] = {"Расчёты": by_type["Расчёты"], "Обороты": by_type["Обороты"], "δ": δ_р}

    # Стоп-отчёт §4.4 — перечень строк реестра с расхождением (не только
    # блокирующих), пара, суммы обеих сторон, разница. Строится по всем
    # строкам, включая те, что не прошли §4.3, — регламент требует перечень,
    # а не только причину первого стопа.
    for (код_а, код_б), by_type in by_pair_type.items():
        for тип, d in by_type.items():
            if d["δ"] == 0:
                continue
            статус = "сверх порога" if abs(d["δ"]) > rules["порог_существенности_вго"] else "в пределах порога"
            result.vgo_lines_report.append({
                "пара": f"«{периметр.каноническое(код_а)}» → «{периметр.каноническое(код_б)}»",
                "тип": тип, "сумма_а": d["а"], "сумма_б": d["б"], "δ": d["δ"], "статус": статус,
            })

    # §4.4 — расхождение сверх порога блокирует.
    for (код_а, код_б), d in pairs_ok.items():
        if abs(d["δ"]) > rules["порог_существенности_вго"]:
            result.add(_f("П-ВГО-4.4-СВЕРХ-ПОРОГА", BLOCKING,
                           f"пара «{периметр.каноническое(код_а)}» → «{периметр.каноническое(код_б)}»: "
                           f"расхождение {abs(d['δ']):,.0f} ₽ превышает порог существенности "
                           f"{rules['порог_существенности_вго']:,.0f} ₽ (регламент §4.4)",
                           "консолидация останавливается, результат не формируется; строка возвращается "
                           "отделу консолидации с суммами обеих сторон",
                           where="реестр ВГО",
                           expected=f"|А−Б| ≤ {rules['порог_существенности_вго']:,.0f} ₽",
                           actual=f"А={d['Расчёты']['а']:,.0f} ₽, Б={d['Расчёты']['б']:,.0f} ₽",
                           delta=d["δ"]))

    return pairs_ok


def apply_corrections_and_eliminations(pairs_ok: dict, by_code: dict, rules: dict, result: Result) -> None:
    периметр = Периметр.из_правил(rules)
    вго = rules["вго"]
    порог = rules["порог_существенности_вго"]
    g_f1, h_f1, g_f2, h_f2 = {}, {}, {}, {}

    def add(d, code, amount):
        d[code] = d.get(code, 0) + amount

    for (код_а, код_б), d in pairs_ok.items():
        δ = d["δ"]
        if δ != 0 and abs(δ) <= порог:
            for term in вго["корректировка_в_пределах_порога"]["статьи"]:
                target = g_f2 if term["статья"].startswith("2") else g_f1
                add(target, term["статья"], term["знак"] * δ)
            result.journal.append({
                "вид": "Корректировка", "пара": периметр.каноническое(код_б),
                "содержание": (f"Доначисление по операциям с {периметр.каноническое(код_а)}: "
                                f"себестоимость (2120) +{δ:,.0f}; кредиторская задолженность (1520) "
                                f"+{δ:,.0f}; нераспределённая прибыль (1370) −{δ:,.0f}"),
                "сумма": δ})
            result.add(_f("П-ВГО-4.2-КОРРЕКТИРОВКА", WARNING,
                           f"пара «{периметр.каноническое(код_а)}» → «{периметр.каноническое(код_б)}»: "
                           f"расхождение {δ:,.0f} ₽ в пределах порога — доначислено у "
                           f"«{периметр.каноническое(код_б)}» по регламенту §4.2",
                           "проверить документ, ставший причиной расхождения, до следующего периода "
                           "(регламент §4.7 — перенос не переносится, считается заново)",
                           where=f"{код_а}→{код_б}", expected=0, actual=δ, delta=δ))

        # §4.5 — элиминация по данным стороны А.
        add(h_f1, вго["расчёты"]["элиминируемая_статья_а"], d["Расчёты"]["а"])
        add(h_f1, вго["расчёты"]["элиминируемая_статья_б"], d["Расчёты"]["а"])
        add(h_f2, вго["обороты"]["элиминируемая_статья_а"], d["Обороты"]["а"])
        add(h_f2, вго["обороты"]["элиминируемая_статья_б"], d["Обороты"]["а"])
        result.journal.append({
            "вид": "Элиминация",
            "пара": f"{периметр.каноническое(код_а)} → {периметр.каноническое(код_б)}",
            "содержание": (f"ДЗ/КЗ {d['Расчёты']['а']:,.0f}; выручка/себестоимость "
                            f"{d['Обороты']['а']:,.0f}"),
            "сумма": d["Расчёты"]["а"]})

    # §4.6 — инвестиции.
    периметр_коды = периметр.коды()
    материнская = next(c for c, з in периметр.записи.items() if з["роль"] == "материнская")
    дочерние = [c for c in периметр_коды if c != материнская]
    if материнская in by_code:
        инв_родителя = _statute_value(by_code[материнская],
                                       next(s for s in rules["статьи_ф1"] if s["код"] == "1170")["слагаемые"])
        σ_ук_дочек = sum(_statute_value(by_code[c],
                                         next(s for s in rules["статьи_ф1"] if s["код"] == "1310")["слагаемые"])
                          for c in дочерние if c in by_code)
        if инв_родителя != σ_ук_дочек:
            result.add(_f("П-ВГО-4.6-ИНВЕСТИЦИИ", BLOCKING,
                           f"финансовые вложения материнской компании (1170={инв_родителя:,.0f} ₽) не "
                           f"равны сумме уставных капиталов дочерних (Σ1310={σ_ук_дочек:,.0f} ₽) — "
                           "регламент §4.6",
                           "консолидация останавливается; сверить ОСВ материнской и дочерних компаний "
                           "по счетам 58 и 80",
                           where="ОСВ (счета 58, 80)", expected=σ_ук_дочек, actual=инв_родителя,
                           delta=инв_родителя - σ_ук_дочек))
        else:
            h_f1["1170"] = инв_родителя
            h_f1["1310"] = σ_ук_дочек
            result.journal.append({"вид": "Элиминация", "пара": "инвестиции ↔ уставный капитал",
                                    "содержание": (f"1170 у материнской −{инв_родителя:,.0f}; 1310 у "
                                                    f"дочерних −{σ_ук_дочек:,.0f}"),
                                    "сумма": инв_родителя})

    result.g_f1, result.h_f1, result.g_f2, result.h_f2 = g_f1, h_f1, g_f2, h_f2


def run_control_checks(result: Result, by_code: dict, rules: dict) -> None:
    """К2–К9 (регламент §5) — считаются двумя независимыми способами, где
    это возможно (правило computational_core.md 4: контроль «сумма
    детальных = итог раздела» на каждом уровне)."""
    периметр = Периметр.из_правил(rules)

    def val(f, code, comp=None):
        d = f.get(code, {})
        return d.get(comp, 0) if comp else sum(d.values())

    f1, f2 = result.company_f1, result.company_f2

    # К2/К3 — каждая компания сама по себе (до элиминаций и корректировок).
    for code, osv in by_code.items():
        активы = val(f1, "1150", code) + val(f1, "1170", code) + val(f1, "1210", code) + \
                 val(f1, "1230", code) + val(f1, "1250", code)
        пассивы = val(f1, "1310", code) + val(f1, "1370", code) + val(f1, "1410", code) + val(f1, "1520", code)
        ok = активы == пассивы
        result.checks.append(_f("К2-Ф1-БАЛАНС-ПО-КОМПАНИИ", INFO if ok else BLOCKING,
                                 f"Ф1 «{периметр.каноническое(code)}»: 1600 = 1700 (до консолидации)",
                                 "сверить перенос остатков ОСВ по таблице соответствия регламента §3",
                                 where=osv.путь, expected=пассивы, actual=активы, delta=активы - пассивы))

        net_by_lines = val(f2, "2110", code) - val(f2, "2120", code) + val(f2, "2340", code) \
            - val(f2, "2350", code) - val(f2, "2410", code)
        net_by_99 = osv.счета.get("99", {}).get("оборот_кт", 0) - osv.счета.get("99", {}).get("оборот_дт", 0)
        ok = int(round(net_by_lines)) == int(round(net_by_99))
        result.checks.append(_f("К3-Ф2-ЧИСТАЯ-ПРИБЫЛЬ", INFO if ok else BLOCKING,
                                 f"Ф2 «{периметр.каноническое(code)}»: 2400 = оборот Кт99 − оборот Дт99",
                                 "сверить обороты по счёту 99 с суммой статей Ф2 — расхождение означает "
                                 "потерянную или задвоенную статью",
                                 where=osv.путь, expected=net_by_99, actual=net_by_lines,
                                 delta=net_by_lines - net_by_99))

    # К4 — консолидировано: 1600 = 1700, посчитано так же, как формулы формы (F+G-H).
    def cons(f, g, h, code):
        return val(f, code) + g.get(code, 0) - h.get(code, 0)

    активы_conc = sum(cons(f1, result.g_f1, result.h_f1, c) for c in ("1150", "1170", "1210", "1230", "1250"))
    пассивы_conc = sum(cons(f1, result.g_f1, result.h_f1, c) for c in ("1310", "1370", "1410", "1520"))
    ok = активы_conc == пассивы_conc
    result.checks.append(_f("К4-КОНС-БАЛАНС", INFO if ok else BLOCKING,
                             "Консолидировано: 1600 = 1700",
                             "проверить корректировки (G) и элиминации (H) — баланс не сошёлся на "
                             "уровне группы, хотя каждая компания сама по себе сходится",
                             expected=пассивы_conc, actual=активы_conc, delta=активы_conc - пассивы_conc))

    # К5 — 1230/1520 консолидированные двумя способами.
    for code, other_ok_key in (("1230", None), ("1520", None)):
        прямая = cons(f1, result.g_f1, result.h_f1, code)
        по_формуле = val(f1, code) + result.g_f1.get(code, 0) - result.h_f1.get(code, 0)
        ok = прямая == по_формуле
        result.checks.append(_f("К5-КОНС-1230-1520", INFO if ok else BLOCKING,
                                 f"{code} конс. = Σ{code} + корректировки − элиминации по «Расчётам»",
                                 "пересчитать элиминацию/корректировку по этой статье",
                                 expected=по_формуле, actual=прямая, delta=прямая - по_формуле))

    # К6 — 2110/2120.
    for code in ("2110", "2120"):
        прямая = cons(f2, result.g_f2, result.h_f2, code)
        по_формуле = val(f2, code) + result.g_f2.get(code, 0) - result.h_f2.get(code, 0)
        ok = прямая == по_формуле
        result.checks.append(_f("К6-КОНС-2110-2120", INFO if ok else BLOCKING,
                                 f"{code} конс. = Σ{code} + корректировки − элиминации по «Оборотам»",
                                 "пересчитать элиминацию/корректировку по этой статье",
                                 expected=по_формуле, actual=прямая, delta=прямая - по_формуле))

    # К7 — 2400/1370 против суммы корректировок δ.
    σ_корр_2120 = result.g_f2.get("2120", 0)
    σ_корр_1370 = result.g_f1.get("1370", 0)
    ok = round(σ_корр_2120 + σ_корр_1370, 2) == 0
    result.checks.append(_f("К7-КОНС-2400-1370", INFO if ok else BLOCKING,
                             "корректировка 2120 (себестоимость, +δ) и 1370 (нераспределённая прибыль, −δ) "
                             "должны в сумме давать ноль — один и тот же δ, две стороны проводки",
                             "сверить знаки корректировки §4.2", expected=0,
                             actual=σ_корр_2120 + σ_корр_1370, delta=σ_корр_2120 + σ_корр_1370))

    # К8 — элиминация не больше статьи.
    for code in ("1230", "1520", "2110", "2120", "1170", "1310"):
        f = f1 if code.startswith("1") else f2
        h = result.h_f1 if code.startswith("1") else result.h_f2
        итог, элим = val(f, code), h.get(code, 0)
        ok = элим <= итог if итог >= 0 else элим >= итог
        result.checks.append(_f("К8-ЭЛИМИНАЦИЯ-НЕ-БОЛЬШЕ-СТАТЬИ", INFO if ok else BLOCKING,
                                 f"элиминация по статье {code} не превышает саму статью (сумма по группе)",
                                 "проверить, не задвоена ли пара ВГО или элиминация",
                                 expected=f"≤ {итог:,.0f}", actual=элим, delta=элим - итог))

    # К9 — форма актив-пассив (по каждой компании и по группе) = 0.
    for code, osv in by_code.items():
        активы = val(f1, "1150", code) + val(f1, "1170", code) + val(f1, "1210", code) + \
                 val(f1, "1230", code) + val(f1, "1250", code)
        пассивы = val(f1, "1310", code) + val(f1, "1370", code) + val(f1, "1410", code) + val(f1, "1520", code)
        result.checks.append(_f("К9-ФОРМА-АКТИВ-ПАССИВ", INFO if активы == пассивы else BLOCKING,
                                 f"форма «{периметр.каноническое(code)}»: актив − пассив = 0",
                                 "см. К2 по этой же компании", where=osv.путь,
                                 expected=0, actual=активы - пассивы, delta=активы - пассивы))
    result.checks.append(_f("К9-ФОРМА-АКТИВ-ПАССИВ", INFO if активы_conc == пассивы_conc else BLOCKING,
                             "форма «консолидировано»: актив − пассив = 0",
                             "см. К4", expected=0, actual=активы_conc - пассивы_conc,
                             delta=активы_conc - пассивы_conc))


def run_quarter(osvs: list[ОСВ], vgo_rows: list[СтрокаВГО], rules: dict) -> Result:
    result = Result()
    by_code = match_companies(osvs, rules, result)
    if result.blocking:
        return result   # периметр не сошёлся — дальше считать нечем и незачем

    f1, f2 = compute_company_lines(by_code, rules, result)
    result.company_f1, result.company_f2 = f1, f2

    pairs_ok = process_vgo(vgo_rows, by_code, rules, result)
    apply_corrections_and_eliminations(pairs_ok, by_code, rules, result)
    if result.blocking:
        return result   # §4.3/§4.4/§4.6 остановили консолидацию — форма не формируется

    run_control_checks(result, by_code, rules)
    return result
