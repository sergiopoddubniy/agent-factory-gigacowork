#!/usr/bin/env python3
"""
run.py — CLI харнеса консолидации. Запускается агентом через коннектор
исполнения кода (подтверждён администратором платформы 17.09.2026:
Python 3.11, openpyxl, pandas, запись в sandbox:/output/, без внешней сети).

Код возврата — часть контракта, не деталь реализации:
  0 — консолидация выполнена, файлы результата записаны;
  2 — консолидация остановлена регламентом (BLOCKING-находка) — это
      ШТАТНЫЙ, ожидаемый исход (регламент §4.3/§4.4/§4.6), не сбой харнеса;
      протокол находок записан, файла формы и журнала нет — намеренно;
  1 — ошибка входа (файл не найден, не читается, формат ОСВ/реестра не
      распознан) — харнес не смог посчитать, а не «посчитал и не сошлось».

    python3 run.py --osv <ОСВ1.xlsx> <ОСВ2.xlsx> <ОСВ3.xlsx> --vgo <ВГО.xlsx> \\
        --form-template <Консолидация_форма.xlsx> --out <папка> \\
        --период "1 квартал 2026 г." [--rules <consolidation_rules.yaml>]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.engine import run_quarter  # noqa: E402
from harness.io_xlsx import read_osv, read_vgo, write_form, write_journal, write_protocol  # noqa: E402

EXIT_OK, EXIT_INPUT_ERROR, EXIT_STOPPED = 0, 1, 2

DEFAULT_RULES = Path(__file__).resolve().parent.parent / "rules" / "consolidation_rules.yaml"


def render_findings(findings: list) -> str:
    out = []
    for f in findings:
        out.append(f"[{f['severity']}] {f['id']}: {f['message']}")
        out.append(f"    fix_hint: {f['fix_hint']}")
        if f.get("where"):
            out.append(f"    where: {f['where']}")
        if f.get("expected") is not None or f.get("actual") is not None:
            out.append(f"    expected={f.get('expected')!r} actual={f.get('actual')!r} delta={f.get('delta')!r}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Харнес консолидации отчётности группы «Северный порт»")
    ap.add_argument("--osv", nargs=3, required=True, metavar=("ОСВ1", "ОСВ2", "ОСВ3"))
    ap.add_argument("--vgo", required=True)
    ap.add_argument("--form-template", required=True)
    ap.add_argument("--out", required=True, help="папка результата (sandbox:/output/<квартал>/)")
    ap.add_argument("--период", required=True, help='например, "1 квартал 2026 г."')
    ap.add_argument("--rules", default=str(DEFAULT_RULES))
    ap.add_argument("--json", action="store_true", help="печатать протокол как JSON вместо текста")
    args = ap.parse_args(argv)

    try:
        rules = yaml.safe_load(Path(args.rules).read_text(encoding="utf-8"))
        osvs = [read_osv(p) for p in args.osv]
        vgo_rows = read_vgo(args.vgo)
    except (FileNotFoundError, ValueError) as e:
        print(f"ОШИБКА ВХОДА: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR

    result = run_quarter(osvs, vgo_rows, rules)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    protocol = {
        "период": args.период,
        "остановлено": result.blocking,
        "находки": result.findings,
        "контроли": result.checks,
        "строки_вго_с_расхождением": result.vgo_lines_report,
    }
    (out_dir / "протокол.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2, default=str),
                                            encoding="utf-8")

    if result.blocking:
        report = ["# Консолидация остановлена — " + args.период, "",
                  "Результат не формируется (регламент — см. находки ниже). Форма, журнал и протокол "
                  "контролей не создаются: заполненный файл при блокирующем нарушении хуже отсутствующего.",
                  ""]
        blocking_findings = [f for f in result.findings + result.checks if f["severity"] == "BLOCKING"]
        report.append("## Причина остановки")
        report.append(render_findings(blocking_findings))
        if result.vgo_lines_report:
            report += ["", "## Строки реестра ВГО с расхождением (все, не только блокирующая)", "",
                       "| Пара | Тип | Сумма А | Сумма Б | Разница (А−Б) | Статус |",
                       "|---|---|---|---|---|---|"]
            for l in result.vgo_lines_report:
                report.append(f"| {l['пара']} | {l['тип']} | {l['сумма_а']:,.0f} | {l['сумма_б']:,.0f} "
                               f"| {l['δ']:,.0f} | {l['статус']} |")
        (out_dir / "СТОП.md").write_text("\n".join(report) + "\n", encoding="utf-8")
        print("\n".join(report))
        return EXIT_STOPPED

    write_form(args.form_template, out_dir / "Консолидация_результат.xlsx", args.период,
               result.company_f1, result.company_f2, result.g_f1, result.h_f1, result.g_f2, result.h_f2)
    write_journal(out_dir / "Журнал_корректировок_и_элиминаций.xlsx", result.journal)
    write_protocol(out_dir / "Протокол_контролей.xlsx", result.checks)

    print(f"Консолидация выполнена — {args.период}")
    print(f"Результат: {out_dir / 'Консолидация_результат.xlsx'}")
    warnings = [f for f in result.findings if f["severity"] == "WARNING"]
    if warnings:
        print("\nПрименённые корректировки (регламент §4.2):")
        print(render_findings(warnings))
    print(f"\nКонтролей проверено: {len(result.checks)} · все OK "
          f"(иначе результат не был бы записан — `result.blocking` проверяется до записи файлов).")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
