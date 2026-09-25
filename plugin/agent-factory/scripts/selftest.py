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

        # --- 2б. Чек-лист полноты: единица результата для реестра вопросов ---
        no_cl = re.sub(r"Чек-лист полноты:\n(?:\d+\. [^\n]+\n)+", "", base)
        assert no_cl != base, "мутация не применена: чек-лист не найден"
        m = write(tmp / "nocl.md", no_cl)
        r = run("spec_readiness.py", "--spec", str(m), "--threshold", "pilot")
        check("мутация: ии-нативный без чек-листа полноты → Ф-08-ЧЕКЛИСТ на пороге pilot",
              r.returncode == 1 and "Ф-08-ЧЕКЛИСТ" in r.stdout)
        r = run("spec_readiness.py", "--spec", str(m))
        check("мутация: без чек-листа порог demo проходит (глубина по классу)", r.returncode == 0)
        m = write(tmp / "nocl_helper.md", no_cl.replace("класс применимости: ии-нативный", "класс применимости: ии-помощник"))
        r = run("spec_readiness.py", "--spec", str(m), "--threshold", "pilot")
        check("класс ии-помощник: чек-лист не требуется", "Ф-08-ЧЕКЛИСТ" not in r.stdout)
        no_crit = re.sub(r" — закрыто, если: [^\n]+", "", base)
        assert no_crit != base, "мутация не применена: критерии закрытия не найдены"
        m = write(tmp / "nocrit.md", no_crit)
        r = run("spec_readiness.py", "--spec", str(m), "--threshold", "pilot")
        check("мутация: пункты чек-листа без критерия закрытия → Ф-08-КРИТЕРИЙ на пороге pilot",
              r.returncode == 1 and "Ф-08-КРИТЕРИЙ" in r.stdout and "1, 2, 3, 4, 5, 6, 7, 8, 9, 10" in r.stdout)
        r = run("spec_readiness.py", "--spec", str(m))
        check("мутация: без критериев порог demo проходит", r.returncode == 0)
        r = run("spec_readiness.py", "--spec", str(spec), "--threshold", "pilot")
        check("пример: у всех пунктов есть критерий закрытия — Ф-08-КРИТЕРИЙ нет", "Ф-08-КРИТЕРИЙ" not in r.stdout)

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
        check("сборка: отпечаток спецификации во фронтматтере", "спецификация: demo-1c-dev-assistant · S8 ·" in skill_text)
        check("сборка: 17 разделов", len(re.findall(r"^## \d+\. ", skill_text, re.M)) == 17)
        check("сборка: чек-лист полноты порождён — таблица из 10 строк, строка сверки, гейт completeness_check, модуль 0",
              "Чек-лист: 10 пунктов" in skill_text and "`completeness_check`" in skill_text
              and "**Модуль 0." in skill_text and skill_text.count("| `закрыто` / `вопрос` / `конфликт` |") == 10)
        check("сборка: одностороннее правило о расхождениях в §10", "выносится человеку **всегда**" in skill_text)
        check("сборка: таблица критериев закрытия — 10 строк, форма описана один раз",
              "| № | Пункт чек-листа | `закрыто`, если |" in skill_text
              and skill_text.count("| `закрыто`, если |") == 1
              and "упоминание, кто заполняет или видит поле, критерий не выполняет" in skill_text)
        cmd_text = (pkg / "commands/run-agent.md").read_text(encoding="utf-8")
        check("сборка: шлюз на источник входных данных не переспрашивает при приложенных файлах",
              "Шлюз на источник входных данных" in skill_text and "не переспрашивая" in cmd_text
              and "Источник входных данных: спросить" not in cmd_text)
        # --- run4 (агент консолидации): коннектор, ветвление модулей, запасной результат ---
        conn_spec = base.replace("Коннекторы: нет", "Коннекторы: 1С OData — чтение метаданных — версия 2.1")
        assert conn_spec != base
        conn_spec = conn_spec.replace("5. Прогнать quality gates → ответ по формату §17",
                                      "5. Вызвать коннектор → код возврата 0 или 2\n"
                                      "6. При коде 2 остановиться → СТОП пользователю\n"
                                      "7. При коде 0 прогнать quality gates → ответ по формату §17")
        assert "6. При коде 2" in conn_spec
        conn_spec = conn_spec.replace("- Прогнать quality gates — модель — нет — таблица чек-листа ровно из 10 строк",
                                      "- Прогнать quality gates — модель — нет — таблица чек-листа ровно из 10 строк\n"
                                      "- Вызвать коннектор — код — соответствия источнику — код возврата и протокол\n"
                                      "- При коде 2 остановиться — модель — нет — СТОП процитирован целиком\n"
                                      "- При коде 0 прогнать quality gates — модель — нет — таблица чек-листа")
        conn_spec = conn_spec.replace("Ядро: нет — причина:", "Ядро: извлекающее\nБыло: нет — причина:")
        r = run("build_agent_package.py", "--spec", str(write(tmp / "conn.md", conn_spec)), "--out", str(tmp / "kit_conn"), "--date", "2026-09-21")
        conn_cmd = (tmp / "kit_conn/demo-1c-dev-assistant/commands/run-agent.md").read_text(encoding="utf-8")
        conn_skill = (tmp / "kit_conn/demo-1c-dev-assistant/skills/00-agent-skill/SKILL.md").read_text(encoding="utf-8")
        check("сборка: коннектор из блока 11 попадает в подтверждённые инструменты команды запуска",
              r.returncode == 0 and "Подтверждённые инструменты: коннектор «1С OData» (чтение метаданных)" in conn_cmd
              and "Подтверждённые инструменты: нет" in cmd_text, r.stdout[-300:])
        check("сборка: шаг «При коде …» — ветвь: вход у точки ветвления, а не у соседней ветви",
              "**Модуль 6 (ветвь).** При коде 2 остановиться." in conn_skill
              and "**Модуль 7 (ветвь).** При коде 0 прогнать quality gates." in conn_skill
              and conn_skill.count("- вход: выход модуля 5 (код возврата 0 или 2)") == 2
              and "выход модуля 6" not in conn_skill)
        # --- run5 (агент реестра закупок): решение о ядре записывается явно ---
        no_core = base.replace("Ядро: нет — причина:", "Ядро-удалено:")
        assert no_core != base
        r = run("spec_readiness.py", "--spec", str(write(tmp / "nocore.md", no_core)))
        check("ворота: нет строки «Ядро:» → Ф-06-ЯДРО, FAIL", r.returncode == 1 and "Ф-06-ЯДРО" in r.stdout, r.stdout[-300:])
        bare_core = base.replace("Ядро: нет — причина:", "Ядро: нет —")
        r = run("spec_readiness.py", "--spec", str(write(tmp / "barecore.md", bare_core)))
        check("ворота: «Ядро: нет» без причины → Ф-06-ЯДРО", r.returncode == 1 and "без причины" in r.stdout, r.stdout[-300:])
        extr = base.replace("Ядро: нет — причина:", "Ядро: извлекающее\nБыло: нет — причина:")
        extr = extr.replace("- Сопоставить требования с объектами выгрузки — модель —", "- Сопоставить требования с объектами выгрузки — код —")
        r = run("spec_readiness.py", "--spec", str(write(tmp / "extr.md", extr)))
        check("ворота: «Ядро: извлекающее» проходит", r.returncode == 0, r.stdout[-300:])
        no_tbl = base.replace("Граница код/модель:", "Граница-удалена:")
        assert no_tbl != base
        r = run("spec_readiness.py", "--spec", str(write(tmp / "notbl.md", no_tbl)))
        check("ворота: нет таблицы «Граница код/модель:» → Ф-08-ГРАНИЦА", r.returncode == 1 and "Ф-08-ГРАНИЦА" in r.stdout, r.stdout[-300:])
        code_row = base.replace("- Сопоставить требования с объектами выгрузки — модель —", "- Сопоставить требования с объектами выгрузки — код —")
        assert code_row != base
        r = run("spec_readiness.py", "--spec", str(write(tmp / "coderow.md", code_row)))
        check("ворота: шаг отдан коду при «Ядро: нет» → Ф-06-ЯДРО (противоречие таблице)",
              r.returncode == 1 and "шаг отдан коду" in r.stdout, r.stdout[-300:])
        r = run("build_agent_package.py", "--spec", str(spec), "--out", str(tmp / "kit_tbl"), "--date", "2026-09-21")
        tbl_skill = (tmp / "kit_tbl/demo-1c-dev-assistant/skills/00-agent-skill/SKILL.md").read_text(encoding="utf-8")
        check("сборка: таблица «шаг → исполнитель → инвариант → чем проверяется» перенесена в §6 навыка",
              r.returncode == 0 and "| Шаг | Исполнитель | Инвариант | Чем проверяется |" in tbl_skill
              and tbl_skill.count("| модель |") == 5 and "ядро: нет — " in tbl_skill)
        no_marker = base.replace("Коннекторы: нет\n", "")
        r = run("spec_readiness.py", "--spec", str(write(tmp / "nomarker.md", no_marker)))
        check("ворота: коннектор упомянут словами без строки «Коннекторы:» → Ф-11-КОННЕКТОР, FAIL",
              r.returncode == 1 and "Ф-11-КОННЕКТОР" in r.stdout, r.stdout[-300:])
        r_nocrit = run("build_agent_package.py", "--spec", str(tmp / "nocrit.md"), "--out", str(tmp / "kit_nocrit"), "--date", "2026-09-21")
        nocrit_text = (tmp / "kit_nocrit/demo-1c-dev-assistant/skills/00-agent-skill/SKILL.md").read_text(encoding="utf-8")
        check("сборка: пункт без критерия получает критерий по умолчанию (прямое называние в источнике)",
              r_nocrit.returncode == 0 and nocrit_text.count("источник называет это прямо") == 10)
        r_nocl = run("build_agent_package.py", "--spec", str(tmp / "nocl.md"), "--out", str(tmp / "kit_nocl"), "--date", "2026-09-21")
        nocl_text = (tmp / "kit_nocl/demo-1c-dev-assistant/skills/00-agent-skill/SKILL.md").read_text(encoding="utf-8")
        check("сборка: без чек-листа — пакет собирается, таблицы и гейта нет (форма не выдумывается)",
              r_nocl.returncode == 0 and "completeness_check" not in nocl_text and "Модуль 0" not in nocl_text)
        sha1 = hashlib.sha256((kit / "demo-1c-dev-assistant.zip").read_bytes()).hexdigest()
        r = run("build_agent_package.py", "--spec", str(spec), "--out", str(kit), "--date", "2026-09-21")
        sha2 = hashlib.sha256((kit / "demo-1c-dev-assistant.zip").read_bytes()).hexdigest()
        check("сборка: детерминированный архив (тот же SHA-256)", sha1 == sha2)
        r = run("build_agent_package.py", "--spec", str(tmp / "m12.md"), "--out", str(tmp / "kit_bad"))
        check("сборка: ниже порога demo → код 1, пакет не порождается",
              r.returncode == 1 and not (tmp / "kit_bad").exists())
        r = run("build_agent_package.py", "--spec", str(tmp / "нет.md"), "--out", str(tmp / "kit_none"))
        check("сборка: нет файла → код 2", r.returncode == 2)

        # --- 4г. Публикация: шлюз запретов и витринная сборка ------------------
        r = run("hub_gate.py", "--папка", str(HERE.parent), "--мягко")
        check("шлюз: репозиторий чист по классу A (мягкий режим, 0 нарушений)", r.returncode == 0, r.stdout[-400:])
        leak = tmp / "leak"
        leak.mkdir()
        # Фикстура собирается из кусков, чтобы сам selftest.py не срабатывал на шлюзе.
        write(leak / "урок.md", "Повод: боевой прогон у ООО «" + "Реальная Компания» 13.09" + ".2026, "
              + "Иван " + "Петрович подтвердил, сайт real" + "client" + ".ru")
        r = run("hub_gate.py", "--папка", str(leak), "--строго")
        check("шлюз: орг-форма, ФИО, домен и дата ловятся строгим режимом",
              r.returncode == 1 and all(k in r.stdout for k in ("[ОРГ-ФОРМА]", "[ФИО]", "[ДОМЕН]", "[ДАТА]")), r.stdout[-400:])
        r = run("hub_gate.py", "--папка", str(leak), "--мягко")
        check("шлюз: мягкий режим ловит класс A и пропускает даты",
              r.returncode == 1 and "[ДАТА]" not in r.stdout and "[ОРГ-ФОРМА]" in r.stdout, r.stdout[-400:])
        r = run("hub_gate.py", "--папка", str(tmp / "нет_папки"), "--строго")
        check("шлюз: нет папки → код 2", r.returncode == 2)
        hub_out = tmp / "hub_out"
        r = run("build_hub.py", "--out", str(hub_out), "--no-check", "--version", "V0", "--date", "2026-09-21")
        hub = hub_out / "hub" / "agent-factory"
        check("витрина: раскладка плагина — plugin.yaml, agents/, commands/, skills/agent-factory/SKILL.md, agent.html, README.md",
              r.returncode == 0 and all((hub / f).exists() for f in ("plugin.yaml", "agents/agent-factory.md", "commands/new-agent.md",
                                                                      "skills/agent-factory/SKILL.md", "agent.html", "README.md")), r.stdout[-400:])
        r = run("hub_gate.py", "--папка", str(hub), "--строго")
        check("витрина: строгий шлюз — 0 нарушений (даты и адреса платформы вырезаны)", r.returncode == 0, r.stdout[-600:])
        hub_pkg = (hub / "skills/agent-factory/references/agent_package.md").read_text(encoding="utf-8")
        check("витрина: правила сохранены, провенанс вырезан",
              "Шесть видов инварианта" in hub_pkg and ("28.08" + ".2026") not in hub_pkg and ("27.08" + ".2026") not in hub_pkg)
        hub_st = (hub / "skills/agent-factory/scripts/selftest_delivery.py").read_text(encoding="utf-8")
        check("витрина: фикстуры в коде не тронуты (строки со строковыми литералами)", '2_демо_V3_13.09.26' in hub_st)
        import zipfile
        zp = hub_out / "agent-factory_hub_V0.zip"
        with zipfile.ZipFile(zp) as z:
            names = z.namelist()
            utf8 = all((zi.flag_bits & 0x800) for zi in z.infolist() if any(ord(c) > 127 for c in zi.filename))
        check("витрина: архив с кириллическими именами в UTF-8, без #U-искажений",
              zp.is_file() and utf8 and any("ПРИЁМОЧНЫЙ_ПРОГОН" in n for n in names) and not any("#U" in n for n in names))
        check("витрина: референсов нет — ни examples/, ни шаблонов в базе; индекс базы пересобран на ноль",
              not (hub / "skills/agent-factory/examples").exists()
              and not any(p.is_dir() for p in (hub / "skills/agent-factory/references/agent_templates").iterdir())
              and "Шаблонов: **0**" in (hub / "skills/agent-factory/references/agent_templates/ИНДЕКС.md").read_text(encoding="utf-8")
              and "examples/1c-dev-assistant" not in (hub / "skills/agent-factory/SKILL.md").read_text(encoding="utf-8"))
        r = run("build_hub.py", "--out", str(tmp / "hub_ex"), "--no-check", "--version", "V0", "--с-примерами")
        check("витрина: --с-примерами возвращает пример и шаблон",
              r.returncode == 0 and (tmp / "hub_ex/hub/agent-factory/skills/agent-factory/examples/1c-dev-assistant/AGENT_SPEC.md").is_file())
        check("витрина: копилки пусты",
              "[]" in (hub / "skills/agent-factory/references/knowledge/registry.yaml").read_text(encoding="utf-8"))
        check("витрина: команды в формате платформы — name по-русски, description, без шапки версии",
              (hub / "commands/new-agent.md").read_text(encoding="utf-8").startswith("---\nname: Новый агент\ndescription:")
              and "**Версия:**" not in (hub / "commands/new-agent.md").read_text(encoding="utf-8"))

        # --- 5. Аудит пакета ----------------------------------------------------        # --- 5. Аудит пакета ----------------------------------------------------
        r = run("check_package.py", "--package", str(pkg), "--spec", str(spec))
        check("аудит: собранный пакет PASS (код 0)", r.returncode == 0 and "audit_status: PASS" in r.stdout)
        bad = tmp / "bad_pkg"
        shutil.copytree(pkg, bad)
        sk = bad / "skills/00-agent-skill/SKILL.md"
        sk.write_text(sk.read_text(encoding="utf-8").replace("## 11. HITL_REQUIRED", "## 11. Валидация человеком"),
                      encoding="utf-8")
        r = run("check_package.py", "--package", str(bad))
        check("аудит: вырезан раздел HITL_REQUIRED → FAIL (заголовок, не подстрока)",
              r.returncode == 1 and "HITL_REQUIRED" in r.stdout)
        bad2 = tmp / "bad_pkg2"
        shutil.copytree(pkg, bad2)
        (bad2 / "README.md").write_text("лишний", encoding="utf-8")
        r = run("check_package.py", "--package", str(bad2))
        check("аудит: лишний файл в пакете → FAIL", r.returncode == 1 and "запрещённый файл" in r.stdout)
        changed = write(tmp / "changed.md", base.replace("Цель: 3 часа и меньше", "Цель: 2 часа и меньше"))
        r = run("check_package.py", "--package", str(pkg), "--spec", str(changed))
        check("аудит: спецификация изменилась после сборки → отпечаток не совпал → FAIL",
              r.returncode == 1 and "отпечаток" in r.stdout)
        r = run("check_package.py", "--package", str(tmp / "нет_пакета"))
        check("аудит: нет папки → код 2", r.returncode == 2)
        bad3 = tmp / "bad_pkg3"
        shutil.copytree(pkg, bad3)
        cmd = bad3 / "commands/run-agent.md"
        cmd.write_text(cmd.read_text(encoding="utf-8").replace("Контур: demo", "Режим: demo"), encoding="utf-8")
        r = run("check_package.py", "--package", str(bad3))
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
        kit_ex = EXAMPLE / "комплект_загрузка" / "demo-1c-dev-assistant"
        r = run("check_package.py", "--package", str(kit_ex), "--spec", str(EXAMPLE / "AGENT_SPEC.md"))
        check("пример: пакет для загрузки в examples/ собран из актуальной спецификации (отпечаток совпал)",
              r.returncode == 0, r.stdout[-300:])
        r = run("check_delivery.py", "--папка", str(EXAMPLE / "комплект"), "--задача", "демонстрация",
                "--поколение", "2")
        check("пример: комплект поставки в examples/ проходит сверку с целевым составом (delivery composition check)",
              r.returncode == 0 and "delivery composition check: PASS" in r.stdout, r.stdout[-600:])

        # --- 8. Комплект поставки из спецификации (delivery_composition.md) ------
        mats = EXAMPLE / "материалы"
        dl = tmp / "поставка"
        r = run("build_delivery.py", "--spec", str(spec), "--out", str(dl), "--задача", "демонстрация",
                "--version", "V1", "--date", "2026-09-21", "--kb", str(mats / "база_знаний"),
                "--synthetic", str(mats / "синтетика"), "--responses", str(mats / "ответы_прогона"),
                "--acceptance", str(accp))
        check("поставка: демонстрация → код 0, сверка состава PASS, аудит PASS",
              r.returncode == 0 and "delivery composition check: PASS" in r.stdout
              and "audit_status: PASS" in r.stdout, r.stdout[-800:])
        agent_dir = next(iter((dl / "04_Пакеты_агентов").glob("1_demo-1c-dev-assistant_V1_*")), None)
        check("поставка: папка = агент — навык, карточка, команды, спецификация .docx, журнал",
              agent_dir is not None and all((agent_dir / f).is_file() for f in (
                  "00_Навык_demo-1c-dev-assistant.md", "01_Карточка_агента_demo-1c-dev-assistant.md",
                  "02_Команды_demo-1c-dev-assistant.md", "03_Спецификация_demo-1c-dev-assistant.docx",
                  "04_ЖУРНАЛ_ИЗМЕНЕНИЙ_demo-1c-dev-assistant.md")))
        check("поставка: в поставке нет архивов, архив для загрузки лежит рядом с SHA-256",
              not list(dl.rglob("*.zip")) and (tmp / "поставка_загрузка" / "demo-1c-dev-assistant.zip").is_file()
              and "SHA-256" in (dl / "СБОРКА.md").read_text(encoding="utf-8"))
        check("поставка: с ответами прогона запасной результат заполнен, предупреждения Д3 нет",
              "[WARN] Д3" not in (dl / "СБОРКА.md").read_text(encoding="utf-8")
              and "Ответ ещё не записан" not in (dl / "08_Сценарий_показа/03_Запасной_результат.md").read_text(encoding="utf-8"))
        r = run("build_delivery.py", "--spec", str(spec), "--out", str(tmp / "поставка_noresp"), "--задача", "демонстрация",
                "--version", "V1", "--client", "Тест", "--date", "2026-09-21", "--kb", str(mats / "база_знаний"),
                "--synthetic", str(mats / "синтетика"))
        check("поставка: без ответов прогона — заготовка запасного результата названа в СБОРКА.md предупреждением Д3",
              r.returncode == 0 and "[WARN] Д3 запасной результат — заготовка" in (tmp / "поставка_noresp/СБОРКА.md").read_text(encoding="utf-8"))
        r = run("build_delivery.py", "--spec", str(tmp / "extr.md"), "--out", str(tmp / "поставка_extr"), "--задача", "демонстрация",
                "--version", "V1", "--client", "Тест", "--date", "2026-09-21", "--kb", str(mats / "база_знаний"),
                "--synthetic", str(mats / "синтетика"))
        check("поставка: «Ядро: извлекающее» → шаг сборки про размещение ядра в песочнице и строка «ядро:» в СБОРКА.md",
              r.returncode == 0
              and "Разместить ядро агента" in (tmp / "поставка_extr/00_Инструкция_по_сборке_пространства.md").read_text(encoding="utf-8")
              and "ядро: извлекающее" in (tmp / "поставка_extr/СБОРКА.md").read_text(encoding="utf-8")
              and "Разместить ядро агента" not in (dl / "00_Инструкция_по_сборке_пространства.md").read_text(encoding="utf-8"),
              r.stdout[-400:])
        check("поставка: точка входа, инструкция по сборке, карта джоб, след переиспользования",
              all((dl / f).is_file() for f in ("00_Как_использовать_этот_комплект.md",
                                               "00_Инструкция_по_сборке_пространства.md",
                                               "00_Карта_джоб.md", "00_ПЕРЕИСПОЛЬЗОВАНИЕ.md")))
        kb_map = dl / "01_База_знаний_workspace/1_demo-1c-dev-assistant/00_ЧТО_ЗДЕСЬ_И_ЧЕГО_ЗДЕСЬ_НЕТ.md"
        check("поставка: карта состава базы знаний называет каждый файл и папку проверок",
              kb_map.is_file() and "`шаблон_ТЗ.md`" in kb_map.read_text(encoding="utf-8")
              and "`проверка/`" in kb_map.read_text(encoding="utf-8"))
        evals = dl / "01_База_знаний_workspace/1_demo-1c-dev-assistant/проверка/evals_demo-1c-dev-assistant.md"
        run_f = dl / "01_База_знаний_workspace/1_demo-1c-dev-assistant/проверка/прогон_2026-09-21.md"
        r = run("check_run.py", "--кейсы", str(evals), "--прогон", str(run_f))
        check("поставка: приёмочные кейсы с машинными признаками из блока 12; check_run на ответах прогона — все блокирующие PASS",
              r.returncode == 0 and "провалено 0" in r.stdout, r.stdout[-500:])
        ev = dl / "02_Discovery_спецификация/EVIDENCE_BUNDLE.md"
        check("поставка: пакет доказательств READY по demo лежит в 02_Discovery", ev.is_file() and "READY" in ev.read_text(encoding="utf-8"))
        card = (agent_dir / "01_Карточка_агента_demo-1c-dev-assistant.md").read_text(encoding="utf-8")
        check("поставка: карточка — id, версия, блок активации по имени, свой навык в перечне",
              "**id:** demo-1c-dev-assistant" in card and "**Версия:** V1" in card
              and "Активируй навык «Ассистент 1С-разработки»" in card and "свой навык агента: создать и прикрепить" in card)
        cmds_t = (agent_dir / "02_Команды_demo-1c-dev-assistant.md").read_text(encoding="utf-8")
        check("поставка: команды с префиксом агента, первая — «Как со мной работать», у каждой своё тело",
              "## 1.0 Как со мной работать" in cmds_t and cmds_t.count("**Инструкции:**") == cmds_t.count("\n## 1.")
              and "## 1.2 Черновик ТЗ по запросу" in cmds_t)
        skill_d = (agent_dir / "00_Навык_demo-1c-dev-assistant.md").read_text(encoding="utf-8")
        check("поставка: фиксированный ответ Режима 0 из пяти блоков — в навыке ровно один раз",
              skill_d.count("Что я делаю:") == 1 and all(k in skill_d for k in ("Что мне дать:", "Что вы получите:",
                                                                                   "Чего я не делаю:", "Мои команды:")))
        from docx_writer import docx_text
        dtext = docx_text(agent_dir / "03_Спецификация_demo-1c-dev-assistant.docx")
        check("поставка: клиентский docx — шесть разделов по scenario_template", all(
            k in dtext for k in ("1. ИИ-агент", "2. Инструкции агента", "3. База знаний", "4. Навыки, коннекторы и команды",
                                 "5. Синтетические данные", "6. Чеклист подготовки")))
        r = run("build_delivery.py", "--spec", str(spec), "--out", str(tmp / "пилот"), "--задача", "пилот",
                "--version", "V1", "--date", "2026-09-21", "--kb", str(mats / "база_знаний"),
                "--synthetic", str(mats / "синтетика"), "--calibration", str(mats / "синтетика"),
                "--responses", str(mats / "ответы_прогона"))
        check("поставка: пилот → надстройка П1–П13 целиком (baseline, регламент, метрики, приёмка, ИБ, доступ, бэклог, контур, методология, модель, evals, прогон)",
              r.returncode == 0 and "надстройка «пилот»" in r.stdout and "delivery composition check: PASS" in r.stdout,
              r.stdout[-800:])
        r = run("build_delivery.py", "--spec", str(spec), "--out", str(tmp / "воркшоп"), "--задача", "воркшоп",
                "--version", "V1", "--date", "2026-09-21", "--kb", str(mats / "база_знаний"),
                "--synthetic", str(mats / "синтетика"), "--calibration", str(mats / "синтетика"))
        check("поставка: воркшоп → единое пространство и мастер-сценарий, сверка PASS",
              r.returncode == 0 and "delivery composition check: PASS" in r.stdout, r.stdout[-600:])
        r = run("build_delivery.py", "--spec", str(spec), "--out", str(tmp / "без_бз"), "--задача", "демонстрация",
                "--version", "V1", "--date", "2026-09-21", "--synthetic", str(mats / "синтетика"))
        check("поставка: мутация — объявленная база знаний не передана → Ф-БЗ, код 1 (комплект без базы знаний — заготовка)",
              r.returncode == 1 and "Ф-БЗ" in r.stdout and "шаблон_ТЗ.md" in r.stdout, r.stdout[-400:])
        r = run("build_delivery.py", "--spec", str(tmp / "m12.md"), "--out", str(tmp / "ниже_порога"), "--задача", "демонстрация")
        check("поставка: спецификация ниже порога demo → комплект не порождается (код 1)",
              r.returncode == 1 and not (tmp / "ниже_порога" / "04_Пакеты_агентов").exists())
        # повторная сборка той же папки: журнал наращивается, прежняя папка версии убрана
        r = run("build_delivery.py", "--spec", str(spec), "--out", str(dl), "--задача", "демонстрация",
                "--version", "V2", "--date", "2026-09-22", "--kb", str(mats / "база_знаний"),
                "--synthetic", str(mats / "синтетика"), "--note", "правка правил блока 8")
        dirs = list((dl / "04_Пакеты_агентов").iterdir())
        j = (dirs[0] / "04_ЖУРНАЛ_ИЗМЕНЕНИЙ_demo-1c-dev-assistant.md").read_text(encoding="utf-8") if dirs else ""
        check("поставка: пересборка V2 — одна папка агента, журнал знает V2 и предыдущую V1",
              r.returncode == 0 and len(dirs) == 1 and dirs[0].name.startswith("1_demo-1c-dev-assistant_V2_")
              and "**Версия:** V2" in j and "**Предыдущая версия:** V1" in j, r.stdout[-400:])

        # --- 9. Перенесённые скрипты исходного скилла: каждый жив и говорит правду --
        r = run("check_run.py", "--самопроверка")
        check("порт: check_run.py --самопроверка зелёный", r.returncode == 0, r.stdout[-300:])
        r = run("agent_registry.py", "--lint")
        check("порт: agent_registry.py --lint (реестр реализованных агентов читается)", r.returncode == 0, r.stdout[-300:])
        r = run("templates.py", "--lint")
        check("порт: templates.py --lint (база шаблонов читается)", r.returncode == 0, r.stdout[-300:])
        r = run("agent_registry.py", "--похожие", "подготовить техническое задание по запросу")
        check("порт: agent_registry.py --похожие отвечает (пустая выдача — тоже ответ)", r.returncode == 0, r.stdout[-300:])
        r = run("token_budget.py", str(ROOT))
        check("порт: token_budget.py — оркестратор SKILL.md в норме (≤5000 токенов, ≤500 строк); справочники читаются по надобности",
              r.returncode in (0, 1, 2) and "порог: 5,000 токенов или 500 строк  →  в норме" in r.stdout, r.stdout[:600])
        r = run("freeze_release.py", "--папка", str(dl), "--заморозить")
        r2 = run("freeze_release.py", "--папка", str(dl), "--сверить")
        (dl / "00_Карта_джоб.md").write_text("x", encoding="utf-8")
        r3 = run("freeze_release.py", "--папка", str(dl), "--сверить")
        check("порт: freeze_release.py — заморозка, сверка PASS, после правки сверка FAIL",
              r.returncode == 0 and r2.returncode == 0 and r3.returncode == 1, (r.stdout + r2.stdout + r3.stdout)[-400:])
        r = run("findability.py", "--папка", str(dl))
        check("порт: findability.py считает находимость (код 0/1)", r.returncode in (0, 1), r.stdout[-300:])
        r = run("context_quality.py", str(agent_dir.parent / dirs[0].name))
        check("порт: context_quality.py оценивает контекст папки агента", r.returncode in (0, 1), (r.stdout + r.stderr)[-300:])
        (tmp / "клиент").mkdir(exist_ok=True)
        r = run("init_client_project.py", "--клиент", "Тест", "--задача", "демонстрация", "--папка", str(tmp / "клиент"))
        check("порт: init_client_project.py разворачивает папку клиента", r.returncode == 0 and (tmp / "клиент" / "CLAUDE.md").is_file(), (r.stdout + r.stderr)[-300:])
        r = run("pack_agent_package.py", "--slug", "demo-1c-dev-assistant", "--skill", str(pkg / "skills/00-agent-skill/SKILL.md"),
                "--command", str(pkg / "commands/run-agent.md"), "--out", str(tmp / "pack"))
        check("порт: pack_agent_package.py упаковывает порождённый пакет (аудит исходного скилла PASS)",
              r.returncode == 0, (r.stdout + r.stderr)[-400:])
        r = run("migrate_layout.py", "--папка", str(dl))
        check("порт: migrate_layout.py на новой раскладке — мигрировать нечего (код 2) либо план (0)", r.returncode in (0, 2), r.stdout[-300:])
        r = run("audit_findings.py")
        check("порт: audit_findings.py — журнал находок сверяется со справочниками (код 0)", r.returncode == 0, r.stdout[-400:])

        # --- 9б. Связанность скилла: каждый референс назван в шаге пайплайна --------
        orch = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        orch += "".join(p.read_text(encoding="utf-8") for p in (ROOT / "references" / "pipelines").glob("*.md"))
        orch += "".join(p.read_text(encoding="utf-8") for p in (ROOT / "commands").glob("*.md"))
        refs = [p.name for p in (ROOT / "references").glob("*.md")]
        unnamed = [n for n in refs if n not in orch]
        check("каждый референс назван в шаге пайплайна, а не только в списке (описан, слинкован, не встроен — применяется постфактум)",
              not unnamed, ", ".join(unnamed))
        broken = [m for m in re.findall(r"`(references/[^`]+\.md|scripts/[^`]+\.(?:py|js))`", orch)
                  if not (ROOT / m).exists()]
        check("все ссылки оркестратора на references/ и scripts/ резолвятся", not broken, ", ".join(broken[:5]))
        named_scripts = [p.name for p in (ROOT / "scripts").glob("*.py")]
        unnamed_s = [n for n in named_scripts if n not in orch and not n.startswith("mutate_")]
        check("каждый скрипт назван в оркестраторе или пайплайне", not unnamed_s, ", ".join(unnamed_s))

        # --- 10. Ворота говорят правду: у каждого скрипта код зависит от вердикта --
        for script in ("spec_readiness.py", "consulting_plan.py", "build_agent_package.py", "build_delivery.py",
                       "check_package.py", "check_delivery.py", "evidence_bundle.py", "check_run.py",
                       "freeze_release.py", "findability.py"):
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
