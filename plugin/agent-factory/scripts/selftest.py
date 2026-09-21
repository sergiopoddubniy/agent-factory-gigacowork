#!/usr/bin/env python3
"""
selftest.py — самопроверка фабрики: ворота обязаны говорить правду.

Каждый скрипт фабрики прогоняется на примере и на его мутациях; проверяется
не только «зелёный на хорошем», но и «красный на плохом» — и что код
возврата совпадает с вердиктом. Стенд, который печатает «FAIL» и выходит с
кодом 0, — отчёт, а не ворота.

Три требования к стенду (из hypothesis_testing исходного скилла):
  1. код возврата зависит от результата;
  2. неприменённая мутация — провал, а не пропуск;
  3. корень берётся от расположения файла, не из абсолютного пути.

    python3 selftest.py [-v]

Код возврата: 0 — все проверки прошли, 1 — есть провалы.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXAMPLE = ROOT / "examples" / "1c-dev-assistant"
PY = sys.executable

RESULTS = []
VERBOSE = "-v" in sys.argv


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(HERE / script), *args], capture_output=True, text=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    if VERBOSE or not ok:
        print(("  ok  " if ok else "  FAIL") + f"  {name}" + (f" — {detail}" if detail and not ok else ""))


def mutate(text: str, block: int, new_body: str | None) -> str:
    """Заменяет тело блока N (None — удаляет заголовок вместе с телом).
    Неприменённая мутация — провал: если блок не найден, поднимаем ошибку."""
    pat = re.compile(r"(^## %d\. [^\n]+\n)(.*?)(?=^## \d+\. |\Z)" % block, re.M | re.S)
    if not pat.search(text):
        raise AssertionError(f"мутация не применена: блок {block} не найден")
    if new_body is None:
        return pat.sub("", text)
    return pat.sub(lambda m: m.group(1) + "\n" + new_body.strip("\n") + "\n\n", text)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def main() -> int:
    base = (EXAMPLE / "AGENT_SPEC.md").read_text(encoding="utf-8")
    acc = (EXAMPLE / "ПРИЁМОЧНЫЙ_ПРОГОН.md").read_text(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="factory_selftest_"))
    try:
        spec = write(tmp / "AGENT_SPEC.md", base)

        # --- 1. Ворота на примере ------------------------------------------
        r = run("spec_readiness.py", "--spec", str(spec))
        check("ворота demo: пример проходит (код 0)", r.returncode == 0 and "вердикт: PASS" in r.stdout, r.stdout[-300:])
        r = run("spec_readiness.py", "--spec", str(spec), "--threshold", "pilot")
        check("ворота pilot: пример красный (код 1)", r.returncode == 1 and "Ф-13-ОРАКУЛ" in r.stdout, r.stdout[-300:])
        r = run("spec_readiness.py", "--spec", str(tmp / "нет.md"))
        check("ворота: нет файла → код 2", r.returncode == 2)
        r = run("spec_readiness.py", "--spec", str(spec), "--json")
        check("ворота: --json содержит отпечаток", r.returncode == 0 and '"fingerprint"' in r.stdout)

        # --- 2. Мутации спецификации → ворота краснеют ----------------------
        m = write(tmp / "m12.md", mutate(base, 12, None))
        r = run("spec_readiness.py", "--spec", str(m))
        check("мутация: без блока 12 → demo FAIL", r.returncode == 1 and "Ф-12-ОТСУТСТВУЕТ" in r.stdout)

        m = write(tmp / "m12b.md", base.replace("`prompt_injection_in_document`", "`injection_case`"))
        r = run("spec_readiness.py", "--spec", str(m))
        check("мутация: нет обязательного случая → Ф-12-ОБЯЗАТЕЛЬНЫЕ", r.returncode == 1 and "Ф-12-ОБЯЗАТЕЛЬНЫЕ" in r.stdout)

        m = write(tmp / "m7.md", mutate(base, 7, ""))
        r = run("spec_readiness.py", "--spec", str(m))
        check("мутация: пустой периметр → demo FAIL", r.returncode == 1 and "Ф-07-ОТСУТСТВУЕТ" in r.stdout)

        m = write(tmp / "m3.md", base.replace(
            "- Метрика: часы аналитика на подготовку ТЗ по одному запросу (от получения запроса до передачи ТЗ на согласование)",
            "- Метрика: улучшить эффективность аналитиков"))
        r = run("spec_readiness.py", "--spec", str(m), "--threshold", "pilot")
        check("мутация: метрика-намерение → Ф-03-НЕИЗМЕРИМО", "Ф-03-НЕИЗМЕРИМО" in r.stdout)

        m = write(tmp / "m9.md", mutate(base, 9, "- Агент аккуратно обрабатывает все случаи."))
        r = run("spec_readiness.py", "--spec", str(m))
        check("мутация: нет строк EARS → Ф-09-EARS", r.returncode == 1 and "Ф-09-EARS" in r.stdout)

        m = write(tmp / "m5.md", mutate(base, 5, "- Роль: аналитик 1С"))
        r = run("spec_readiness.py", "--spec", str(m))
        check("мутация: нет триггера → Ф-05-ПОЛЯ", r.returncode == 1 and "Ф-05-ПОЛЯ" in r.stdout)

        det = mutate(mutate(base, 2, None), 4, None).replace(
            "класс применимости: ии-нативный", "класс применимости: детерминированный")
        m = write(tmp / "det.md", det)
        r = run("spec_readiness.py", "--spec", str(m))
        check("класс детерминированный: без PR/FAQ и решения demo PASS", r.returncode == 0, r.stdout[-300:])
        m = write(tmp / "nat.md", mutate(mutate(base, 2, None), 4, None))
        r = run("spec_readiness.py", "--spec", str(m))
        check("класс ии-нативный: без PR/FAQ → Ф-02-МОЛЧАНИЕ", r.returncode == 1 and "Ф-02-МОЛЧАНИЕ" in r.stdout)

        m = write(tmp / "fm.md", base.replace("id: demo-1c-dev-assistant", "id: Demo_1C"))
        r = run("spec_readiness.py", "--spec", str(m))
        check("фронтматтер: плохой id → Ф-00-РАЗБОР", r.returncode == 1 and "Ф-00-РАЗБОР" in r.stdout)

        # --- 3. План добора ---------------------------------------------------
        r = run("consulting_plan.py", "--spec", str(spec), "--threshold", "pilot")
        check("план: выводится из дыр (блоки 3 и 13)", r.returncode == 0 and "эталонные разборы" in r.stdout
              and "Блок 3" in r.stdout and "Блок 13" in r.stdout, r.stdout[-300:])
        ready = base.replace(
            "- Допущение: бейзлайн 6 часов взят по словам руководителя направления, хронометраж не проводился\n", "")
        ready = ready.replace("- Бейзлайн: 6 часов на ТЗ средней сложности по журналу задач за прошлый квартал",
                              "- Бейзлайн: 6,2 часа — хронометраж 8 ТЗ за 2 недели, журнал задач направления")
        ready = mutate(ready, 13, "- Пара 1: запрос на реквизит «Основание закупки» → принятое ТЗ (файл эталон_01.md)\n"
                                  "- Пара 2: запрос на отчёт по остаткам → принятое ТЗ (файл эталон_02.md)\n"
                                  "- Инструкция: доработка «Заявка на закупку» → принятая инструкция (эталон_03.md)")
        ready = mutate(ready, 15, "- Владелец процесса: руководитель направления 1С\n"
                                  "- Принимает: владелец процесса по приёмочным случаям §12\n- Дата приёмки: день 6 пилота")
        ready = mutate(ready, 11, "- Уровень L0: только файлы, загруженные аналитиком.\n"
                                  "- Запись в 1С запрещена на уровне навыка (`external_write=false`).\n"
                                  "- Персональные данные в ТЗ не переносятся без необходимости.")
        rs = write(tmp / "ready.md", ready)
        r = run("spec_readiness.py", "--spec", str(rs), "--threshold", "pilot")
        check("пилот-готовая спецификация: pilot PASS", r.returncode == 0, r.stdout[-400:])
        r = run("consulting_plan.py", "--spec", str(rs), "--threshold", "pilot")
        check("план: на готовой спецификации «добор не нужен»", r.returncode == 0 and "Добор не нужен" in r.stdout)
        r = run("consulting_plan.py", "--spec", str(tmp / "нет.md"))
        check("план: нет файла → код 2", r.returncode == 2)

        # --- 4. Сборка пакета -------------------------------------------------
        kit = tmp / "kit"
        r = run("build_agent_package.py", "--spec", str(spec), "--out", str(kit), "--date", "2026-09-21")
        pkg = kit / "demo-1c-dev-assistant"
        check("сборка: пример → код 0, аудит PASS", r.returncode == 0 and "audit_status: PASS" in r.stdout, r.stdout[-400:])
        check("сборка: структура пакета ровно из двух файлов",
              (pkg / "skills/00-agent-skill/SKILL.md").is_file() and (pkg / "commands/run-agent.md").is_file()
              and len([p for p in pkg.rglob("*") if p.is_file()]) == 2)
        check("сборка: комплект — копия спецификации, переиспользование, СБОРКА.md, архив",
              all((kit / f).is_file() for f in ("AGENT_SPEC.md", "00_ПЕРЕИСПОЛЬЗОВАНИЕ.md", "СБОРКА.md",
                                                 "demo-1c-dev-assistant.zip")))
        skill_text = (pkg / "skills/00-agent-skill/SKILL.md").read_text(encoding="utf-8")
        check("сборка: отпечаток спецификации во фронтматтере", "спецификация: demo-1c-dev-assistant · S1 ·" in skill_text)
        check("сборка: 17 разделов", len(re.findall(r"^## \d+\. ", skill_text, re.M)) == 17)
        sha1 = hashlib.sha256((kit / "demo-1c-dev-assistant.zip").read_bytes()).hexdigest()
        r = run("build_agent_package.py", "--spec", str(spec), "--out", str(kit), "--date", "2026-09-21")
        sha2 = hashlib.sha256((kit / "demo-1c-dev-assistant.zip").read_bytes()).hexdigest()
        check("сборка: детерминированный архив (тот же SHA-256)", sha1 == sha2)
        r = run("build_agent_package.py", "--spec", str(tmp / "m12.md"), "--out", str(tmp / "kit_bad"))
        check("сборка: ниже порога demo → код 1, пакет не порождается",
              r.returncode == 1 and not (tmp / "kit_bad").exists())
        r = run("build_agent_package.py", "--spec", str(tmp / "нет.md"), "--out", str(tmp / "kit_none"))
        check("сборка: нет файла → код 2", r.returncode == 2)

        # --- 5. Аудит пакета ----------------------------------------------------
        r = run("check_delivery.py", "--package", str(pkg), "--spec", str(spec))
        check("аудит: собранный пакет PASS (код 0)", r.returncode == 0 and "audit_status: PASS" in r.stdout)
        bad = tmp / "bad_pkg"
        shutil.copytree(pkg, bad)
        sk = bad / "skills/00-agent-skill/SKILL.md"
        sk.write_text(sk.read_text(encoding="utf-8").replace("## 11. HITL_REQUIRED", "## 11. Валидация человеком"),
                      encoding="utf-8")
        r = run("check_delivery.py", "--package", str(bad))
        check("аудит: вырезан раздел HITL_REQUIRED → FAIL (заголовок, не подстрока)",
              r.returncode == 1 and "HITL_REQUIRED" in r.stdout)
        bad2 = tmp / "bad_pkg2"
        shutil.copytree(pkg, bad2)
        (bad2 / "README.md").write_text("лишний", encoding="utf-8")
        r = run("check_delivery.py", "--package", str(bad2))
        check("аудит: лишний файл в пакете → FAIL", r.returncode == 1 and "запрещённый файл" in r.stdout)
        changed = write(tmp / "changed.md", base.replace("Цель: 3 часа и меньше", "Цель: 2 часа и меньше"))
        r = run("check_delivery.py", "--package", str(pkg), "--spec", str(changed))
        check("аудит: спецификация изменилась после сборки → отпечаток не совпал → FAIL",
              r.returncode == 1 and "отпечаток" in r.stdout)
        r = run("check_delivery.py", "--package", str(tmp / "нет_пакета"))
        check("аудит: нет папки → код 2", r.returncode == 2)
        bad3 = tmp / "bad_pkg3"
        shutil.copytree(pkg, bad3)
        cmd = bad3 / "commands/run-agent.md"
        cmd.write_text(cmd.read_text(encoding="utf-8").replace("Контур: demo", "Режим: demo"), encoding="utf-8")
        r = run("check_delivery.py", "--package", str(bad3))
        check("аудит: команда без «Контур» → FAIL", r.returncode == 1 and "Контур" in r.stdout)

        # --- 6. Пакет доказательств --------------------------------------------
        accp = write(tmp / "acc.md", acc)
        r = run("evidence_bundle.py", "--spec", str(spec), "--package", str(pkg), "--acceptance", str(accp),
                "--out", str(tmp / "ev"))
        check("доказательства: demo READY (код 0), файлы записаны",
              r.returncode == 0 and (tmp / "ev/EVIDENCE_BUNDLE.md").is_file() and (tmp / "ev/EVIDENCE_BUNDLE.json").is_file(),
              r.stdout[-300:] + r.stderr[-300:])
        r = run("evidence_bundle.py", "--spec", str(spec), "--package", str(pkg), "--acceptance", str(accp),
                "--threshold", "pilot")
        check("доказательства: pilot NOT READY (код 1) — ворота и гипотеза",
              r.returncode == 1 and "ворота pilot FAIL" in r.stdout and "гипотеза результата не проверена" in r.stdout)
        accf = write(tmp / "acc_fail.md", acc.replace("Факт: раздел на месте, пометка и причина есть\nВердикт: PASS",
                                                      "Факт: раздел удалён\nВердикт: FAIL\nПричина: раздел удалён"))
        assert accf.read_text(encoding="utf-8") != acc, "мутация прогона не применена"
        r = run("evidence_bundle.py", "--spec", str(spec), "--package", str(pkg), "--acceptance", str(accf))
        check("доказательства: FAIL в прогоне → NOT READY (код 1)", r.returncode == 1 and "template_section_not_applicable" in r.stdout)
        r = run("evidence_bundle.py", "--spec", str(spec), "--package", str(pkg), "--acceptance", str(tmp / "нет.md"))
        check("доказательства: нет файла прогона → код 2", r.returncode == 2)
        r = run("evidence_bundle.py", "--spec", str(changed), "--package", str(pkg))
        check("доказательства: пакет не из этой спецификации → NOT READY", r.returncode == 1 and "аудит пакета FAIL" in r.stdout)

        # Полный пилотный контур: готовая спецификация → пакет → прогон с проверенной гипотезой → READY.
        kit2 = tmp / "kit2"
        r = run("build_agent_package.py", "--spec", str(rs), "--out", str(kit2), "--date", "2026-09-21")
        pkg2 = kit2 / "demo-1c-dev-assistant"
        check("пилотный контур: сборка готовой спецификации PASS", r.returncode == 0, r.stdout[-300:])
        acc_ok = acc.replace("Статус: не проверена", "Статус: подтверждена")
        assert acc_ok != acc, "мутация статуса гипотезы не применена"
        accok = write(tmp / "acc_ok.md", acc_ok)
        r = run("evidence_bundle.py", "--spec", str(rs), "--package", str(pkg2), "--acceptance", str(accok),
                "--threshold", "pilot")
        check("пилотный контур: pilot READY (код 0)", r.returncode == 0 and "READY" in r.stdout, r.stdout[-500:])

        # --- 7. Пример в репозитории собран из актуальной спецификации ----------
        kit_ex = EXAMPLE / "комплект" / "demo-1c-dev-assistant"
        r = run("check_delivery.py", "--package", str(kit_ex), "--spec", str(EXAMPLE / "AGENT_SPEC.md"))
        check("пример: комплект в examples/ собран из актуальной спецификации (отпечаток совпал)",
              r.returncode == 0, r.stdout[-300:])

        # --- 8. Ворота говорят правду: у каждого скрипта код зависит от вердикта --
        for script in ("spec_readiness.py", "consulting_plan.py", "build_agent_package.py",
                       "check_delivery.py", "evidence_bundle.py"):
            src = (HERE / script).read_text(encoding="utf-8")
            check(f"стенд: {script} завершает sys.exit(main()) — код возврата уходит наружу",
                  "sys.exit(main())" in src)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(RESULTS)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nselftest: проверок {total}, провалов {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
