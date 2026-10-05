#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мутации инструмента сборки: аудит обязан отказывать на испорченном навыке
и не отказывать на верном. Оба направления одинаково важны.

Код возврата: 0 — все мутации пойманы и метаморфные отношения соблюдены,
1 — иначе. Обоснование — в шапке `mutate_landed.py`.

**Про фикстуру.** Раньше она была одна и лежала внешним путём: сначала пакет
из старого прогона (после смены формата фронтматтера перестал проходить
аудит), потом пакет клиента (исчез вместе с сессией). В обоих случаях стенд
переставал измерять, но выглядело это как дефект продукта.

Теперь фикстура **генерируется здесь же** по текущим требованиям сборщика.
Она не может ни устареть, ни пропасть. Плата за это названа честно: стенд
перестал быть дифференциальным — он больше не проверяет правила на боевом
артефакте. Эту роль выполняет `selftest.py --against`, и подменять одно
другим нельзя.

Внешний пакет можно подать явно: `--фикстура <путь к .zip или папке>`.
"""
import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
import zipfile

S = pathlib.Path(__file__).resolve().parent.parent

SECTIONS = [
    "Назначение", "Пользователь и триггер", "In-scope", "Out-of-scope",
    "Входные данные", "Внутренние модули выполнения", "Порядок выполнения",
    "Форматы выходных артефактов", "Правила доказательности и источников",
    "Работа с неопределённостью", "HITL_REQUIRED",
    "Demo-режим и SIM_TOOL_CALL / SIM_TOOL_RESULT",
    "Защита данных и prompt injection", "Quality gates",
    "Встроенные acceptance cases", "Ограничения pilot / production",
    "Формат итогового ответа пользователю",
]

GATES = ("scope_check input_check source_check consistency_check output_check "
         "safety_check demo_check handoff_check")
CASES = ("happy_path missing_required_input conflicting_sources "
         "prompt_injection_in_document external_action_without_confirmation")


def make_skill() -> str:
    body = []
    for i, s in enumerate(SECTIONS, 1):
        body.append(f"## {i}. {s}\n")
        if s == "Quality gates":
            body.append("\n".join(f"- `{g}`" for g in GATES.split()) + "\n")
        elif s == "Встроенные acceptance cases":
            body.append("\n".join(f"- `{c}`" for c in CASES.split()) + "\n")
        elif s.startswith("Demo-режим"):
            body.append("Внешние действия только как `SIM_TOOL_CALL` / "
                        "`SIM_TOOL_RESULT`; `external_write=false`.\n")
        else:
            body.append("текст раздела\n")
    return ("---\n"
            "name: Демонстрационный расчёт\n"
            "description: Выполни расчёт по комплекту документов одной задачи или "
            "перепроверь собранный результат. Не брать навык — выбор поставщика; "
            "юридическая проверка договора\n"
            "category: Аналитика\n"
            "version: 1\n"
            "tags: [demo-agent]\n"
            "---\n\n"
            "# Демонстрационный расчёт\n\n"
            "### Когда использовать (триггеры)\n\n"
            "Пользователь принёс комплект документов по одной задаче и просит "
            "выполнить расчёт либо перепроверить уже собранный результат.\n\n"
            "Не бери навык, если речь о выборе поставщика или юридической "
            "проверке договора — это не задачи этого навыка.\n\n" + "\n".join(body))


COMMAND = ("---\nname: run-agent\ndescription: Запуск демонстрационного агента — "
           "полный порядок выполнения из навыка\n---\n\n"
           "# 1 Выполни расчёт\n\n"
           "Активируй навык «Демонстрационный расчёт» — единственный навык "
           "этого агента.\n\n"
           "## Параметры запуска\n\n"
           "Контур: demo\nИсточник входных данных: спросить\n")


def fixture_from(path: pathlib.Path):
    d = pathlib.Path(tempfile.mkdtemp())
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            z.extractall(d)
        root = next(p for p in d.iterdir() if p.is_dir())
    else:
        shutil.copytree(path, d / path.name)
        root = d / path.name
    return d, root / "skills/00-agent-skill/SKILL.md", root / "commands/run-agent.md"


def fixture_generated():
    d = pathlib.Path(tempfile.mkdtemp())
    sk = d / "skills/00-agent-skill/SKILL.md"
    cmd = d / "commands/run-agent.md"
    sk.parent.mkdir(parents=True)
    cmd.parent.mkdir(parents=True)
    sk.write_text(make_skill(), encoding="utf-8")
    cmd.write_text(COMMAND, encoding="utf-8")
    return d, sk, cmd


def build(skill, cmd, slug="test-agent"):
    out = pathlib.Path(tempfile.mkdtemp())
    r = subprocess.run([sys.executable, str(S / "scripts/pack_agent_package.py"),
                        "--slug", slug, "--skill", str(skill), "--command", str(cmd),
                        "--out", str(out)], capture_output=True, text=True)
    shutil.rmtree(out, ignore_errors=True)
    return r.returncode, (r.stdout + r.stderr)


ap = argparse.ArgumentParser()
ap.add_argument("--фикстура", dest="fx", default=None)
args = ap.parse_args()

if args.fx:
    p = next((pathlib.Path(unicodedata.normalize(f, args.fx))
              for f in ("NFC", "NFD")
              if pathlib.Path(unicodedata.normalize(f, args.fx)).exists()), None)
    if p is None:
        print(f"  фикстура не найдена: {args.fx!r}")
        sys.exit(1)
    unpack, origin = (lambda: fixture_from(p)), f"внешняя: {p.name}"
else:
    unpack, origin = fixture_generated, "сгенерирована по текущим требованиям"

d, sk, cmd = unpack()
rc, log = build(sk, cmd)
if rc:
    print(f"  ФИКСТУРА НЕ ПРОХОДИТ АУДИТ ({origin}) — стенд не измерял ничего.")
    for line in [l for l in log.splitlines() if l.strip().startswith("- ")][:3]:
        print("   ", line.strip()[:110])
    shutil.rmtree(d, ignore_errors=True)
    sys.exit(1)
print(f"  фикстура собирается ({origin})")
shutil.rmtree(d, ignore_errors=True)

MUT = [
    ("вырезан раздел HITL_REQUIRED целиком",
     lambda t: re.sub(r"\n#+\s*\d*[.)]?\s*HITL_REQUIRED.*?(?=\n#+\s)", "\n", t, flags=re.S)),
    ("вырезан раздел Quality gates целиком",
     lambda t: re.sub(r"\n#+\s*\d*[.)]?\s*Quality gates.*?(?=\n#+\s)", "\n", t, flags=re.S)),
    ("вырезан раздел Demo-режим целиком",
     lambda t: re.sub(r"\n#+\s*\d*[.)]?\s*Demo-режим.*?(?=\n#+\s)", "\n", t, flags=re.S)),
    ("вырезан раздел Out-of-scope целиком",
     lambda t: re.sub(r"\n#+\s*\d*[.)]?\s*Out-of-scope.*?(?=\n#+\s)", "\n", t, flags=re.S)),
    ("description сокращено до названия",
     lambda t: re.sub(r"^description: .*$", "description: Расчёт", t, count=1, flags=re.M)),
    ("name заменено служебным слагом",
     lambda t: re.sub(r"^name: .*$", "name: demo-agent-skill", t, count=1, flags=re.M)),
    ("version возвращён к константе 1.0.0",
     lambda t: re.sub(r"^version: .*$", "version: 1.0.0", t, count=1, flags=re.M)),
    ("description записан блочным скаляром",
     lambda t: re.sub(r"^description: (.*)$", r"description: |\n  \1", t, count=1, flags=re.M)),
    ("заголовок навыка в старом формате (имя навыка / когда применять)",
     lambda t: re.sub(r"^name: (.*)$", r"имя навыка: \1", t, count=1, flags=re.M)
               .replace("description: ", "когда применять: ", 1)),
    ("вырезан раздел «Когда использовать (триггеры)»",
     lambda t: re.sub(r"\n#{2,4}\s*Когда использовать.*?(?=\n#{1,4}\s)", "\n", t, count=1, flags=re.S)),
]

missed, unapplied = [], []
for name, fn in MUT:
    d, sk, cmd = unpack()
    o = sk.read_text(encoding="utf-8")
    n = fn(o)
    if n == o:
        unapplied.append(name)
        print(f"  НЕ ПРИМЕНИЛАСЬ · {name} — текст не изменился")
        shutil.rmtree(d, ignore_errors=True)
        continue
    sk.write_text(n, encoding="utf-8")
    rc, _ = build(sk, cmd)
    if not rc:
        missed.append(name)
    print(f"  {'поймана     ' if rc else 'ПРОПУЩЕНА   '} · {name}")
    shutil.rmtree(d, ignore_errors=True)

# Метаморфное: разложенный юникод не должен менять вердикт
d, sk, cmd = unpack()
sk.write_text(unicodedata.normalize("NFD", sk.read_text(encoding="utf-8")), encoding="utf-8")
rc, log = build(sk, cmd)
false_reject = bool(rc)
print(f"\n  {'верно       ' if not rc else 'ЛОЖНЫЙ ОТКАЗ'} · "
      f"NFD-нормализация не меняет вердикт")
if rc:
    for line in [l for l in log.splitlines() if l.strip().startswith("- ")][:2]:
        print("   ", line.strip()[:110])
shutil.rmtree(d, ignore_errors=True)

print(f"\nвсего мутаций: {len(MUT)} · не ловится: {len(missed)} · "
      f"не применилось: {len(unapplied)} · ложных отказов: {int(false_reject)}")
sys.exit(1 if (missed or unapplied or false_reject) else 0)
