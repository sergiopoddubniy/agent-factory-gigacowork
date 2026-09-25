#!/usr/bin/env python3
"""
build_agent_card.py — сборка build-kit агента для Поколения 1 / 1.5
(карточка в конструкторе GigaCowork, см. references/agent_card_kit.md).

В отличие от build_agent_package.py (Поколение 2, ZIP грузится в платформу
как файл), здесь риск другой: не лишние файлы, а рассинхрон между SKILL.md
(источник истины) и производными полями карточки. Поэтому вместо
structural audit — consistency_check: числа и названия должны буквально
совпадать между SKILL.md, описанием карточки, текстом поля «Инструкция» и
списком команд.

Собирает build-kit:

    <N>_<agent-slug>/
    ├── 00_Инструкция_по_сборке.md   (не генерируется, копируется как есть)
    ├── 01_Агент_<slug>.md           (поля карточки И текст «Инструкции»)
    ├── 02_Команды_<slug>.md
    ├── 03_Спецификация_<slug>.docx  (если передана)
    └── SKILL.md

Структура выверена на боевом портфеле из 7 агентов: карточка и инструкция
лежат одним файлом, поэтому один и тот же путь передаётся и в --card,
и в --instruction — это штатный вызов, а не обходной.

Карточка и инструкция передаются ОДНИМ файлом в оба аргумента --card и
--instruction: это штатный вызов, второй файл при этом не создаётся. Если
пути разные, инструкция ляжет отдельным файлом 01б.

и упаковывает в ZIP — не для загрузки в платформу, а как контейнер для
ручного переноса полей (см. agent_card_kit.md).

При BLOCKING-нарушениях архив **физически не создаётся** (код возврата 1).
Это надёжнее предупреждения рядом с готовым файлом: предупреждения рядом с
результатом перестают читать, и несогласованный кит уходит в перенос.

Нарушения возвращаются как findings с полями id / severity / message /
fix_hint / where — тот же контракт обратной связи, что у расчётного
harness, чтобы агент правил причину, а не угадывал её по тексту.

Использование:

    python3 build_agent_card.py \
        --slug tender-docs-analyst \
        --agent-number 2 \
        --skill /work/SKILL.md \
        --card /work/01_Агент.md \
        --instruction /work/01_Агент.md \
        --commands /work/02_Команды.md \
        --kb /work/База_знаний/2_tender-docs-analyst \
        --out <папка выдачи>

Флаг --kb включает самую ценную проверку: файлы, на которые ссылаются
команды, действительно лежат в базе знаний агента. Ненайденный файл — это
провал демонстрации при клиенте, а не техническая мелочь.

Флаг --agent-number обязателен только в портфельном режиме (когда агент —
один из нескольких в общем пространстве); для одиночного агента вне
портфеля не передавай его — тогда проверка префикса команд пропускается.

Только stdlib, никаких зависимостей.
"""

from __future__ import annotations  # аннотации X | Y без требования Python 3.10+

import argparse
import hashlib
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path

# Ссылки на файлы внутри команд: `demo/01_извещение.md`, `приемка/README.md`
FILE_REF_RE = re.compile(r"`([^`\n]+\.(?:md|xlsx|xls|docx|doc|pdf|csv|txt|json|yaml))`", re.I)


def nfc(s: str) -> str:
    """Приводит текст к NFC.

    macOS хранит имена файлов в NFD («ё» = «е» + диакритика), и то же самое
    бывает с содержимым: текст, скопированный из Finder или прошедший через
    некоторые редакторы, приезжает разложенным. Тогда «Активируй навык» из
    файла не равно «активируй навык» из константы — при визуально одинаковых
    строках. Нормализовать нужно **и имена файлов, и весь читаемый текст**:
    иначе проверки блока активации и состава разделов дают ложный BLOCKING
    на совершенно корректном ките, и собрать его становится невозможно."""
    return unicodedata.normalize("NFC", s)

DEFAULT_WORK = Path(tempfile.gettempdir()) / "_agent_card_build"

REQUIRED_SECTIONS = [
    "Назначение",
    "Пользователь и триггер",
    "In-scope",
    "Out-of-scope",
    "Входные данные",
    "Внутренние модули выполнения",
    "Порядок выполнения",
    "Форматы выходных артефактов",
    "Правила доказательности",
    "Работа с неопределённостью",
    "HITL_REQUIRED",
    "Demo-режим",
    "Защита данных",
    "Quality gates",
    "acceptance cases",
    "Ограничения pilot / production",
    "Формат итогового ответа",
]

ACTIVATION_PHRASES = [
    "активируй навык",
    "ты всегда начинаешь работу с чтения и активации навыка",
]

BLOCKING = "BLOCKING"   # build-kit не собирается
# Единые коды возврата с build_agent_package.py: 0 — ок, 1 — проверка не
# пройдена, 2 — проблема со входом. Разная семантика одного кода в двух
# скриптах одного пакета — ловушка для того, кто их скриптует.
WARNING = "WARNING"     # собирается, но с пометкой

EXIT_OK, EXIT_AUDIT_FAIL, EXIT_INPUT_ERROR = 0, 1, 2

# Фиксированная метка времени в архиве — тот же SHA-256 на том же содержимом.
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)

NUMBER_RE = re.compile(r"(?<![\w.,])\d{2,}(?:[.,]\d+)?(?:\s?%)?(?![\w])")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Режим ловится и в «ёлочках», и в прямых кавычках, с любыми пробелами:
# из Word текст приезжает то так, то этак, а проверка, молча вернувшая
# пустое множество из-за неузнанной кавычки, — это ложный шлюз.
MODE_RE = re.compile(r"\[\s*РЕЖИМ\s*[«\"'„]([^»\"'“]+)[»\"'“]\s*\]", re.IGNORECASE)

# Шум, который нельзя принимать за содержательные числа: даты, время,
# семверы, номера ГОСТ/ТУ. Без этого NUMBER_DRIFT срабатывает на каждом
# файле с датой в шапке, к нему привыкают и перестают читать — а проверка,
# которую перестали читать, не работает вовсе.
NOISE_RE = re.compile(
    r"\b\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}\b"      # дд.мм.гггг
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"               # 10:00
    r"|\bv?\d+\.\d+(?:\.\d+)+\b"                   # 1.0.0
    r"|\b(?:ГОСТ|ТУ|ISO|СНиП|СП)\s*[\d.\-]+\b"     # ГОСТ 12345-67
    r"|\b(?:19|20)\d{2}\b"                          # годы
)


def read(path: Path, label: str) -> str:
    """Читает входной файл. Отсутствующий файл — это findings, а не traceback:
    консультант, опечатавшийся в пути, должен увидеть, что именно не найдено,
    а не стек вызовов Python."""
    try:
        # NFC на входе: дальше весь текст сравнивается с константами в NFC,
        # и разложенная форма давала бы ложный BLOCKING на корректном файле.
        return nfc(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"consistency_status: FAIL\nblocking: 1\nfindings:\n"
              f"  - id: INPUT_FILE_NOT_FOUND\n    severity: BLOCKING\n"
              f"    message: не найден входной файл «{label}»\n"
              f"    fix_hint: Проверь путь в аргументе --{label}. "
              f"Build-kit не собран.\n    where: {{'path': '{path}'}}")
        sys.exit(EXIT_INPUT_ERROR)
    except UnicodeDecodeError:
        print(f"consistency_status: FAIL\nblocking: 1\nfindings:\n"
              f"  - id: INPUT_FILE_NOT_UTF8\n    severity: BLOCKING\n"
              f"    message: файл «{label}» не читается как UTF-8\n"
              f"    fix_hint: Пересохрани файл в UTF-8 без BOM. "
              f"Типовая причина — файл сохранён из Windows в cp1251.\n"
              f"    where: {{'path': '{path}'}}")
        sys.exit(EXIT_INPUT_ERROR)


def extract_numbers(text: str) -> set:
    """Содержательные числа текста: пороги, количества, KPI. Даты, время,
    версии и номера стандартов вычищаются заранее — иначе проверка шумит
    на каждом файле и её перестают читать."""
    return set(NUMBER_RE.findall(NOISE_RE.sub(" ", text)))


def extract_modes(text: str) -> set:
    """Названия режимов. Возвращает также признак «в тексте вообще есть
    похожие на режим конструкции» — чтобы отличить «режимов нет» от
    «режимы есть, но не распознались»."""
    return {m.strip() for m in MODE_RE.findall(text)}


def looks_like_modes(text: str) -> bool:
    """Есть ли в тексте что-то вида [РЕЖИМ ...], пусть и в нераспознанном
    оформлении. Нужно, чтобы поймать случай, когда регулярка не сработала
    из-за экзотических кавычек и сравнение множеств прошло вхолостую."""
    return bool(re.search(r"\[\s*РЕЖИМ", text, re.IGNORECASE))


def finding(fid, severity, message, fix_hint, where=None, expected=None, actual=None):
    """Контракт обратной связи: не строка для человека, а объект, по которому
    агент может действовать без догадок. Поле fix_hint прямо говорит, что
    править, — иначе агент угадывает причину по тексту сообщения."""
    f = {"id": fid, "severity": severity, "message": message, "fix_hint": fix_hint}
    if where is not None:
        f["where"] = where
    if expected is not None:
        f["expected"] = expected
    if actual is not None:
        f["actual"] = actual
    return f


def audit(skill_text: str, card_text: str, instr_text: str, cmd_text: str,
          slug: str, agent_number: str | None, kb_dir: Path | None = None) -> dict:
    findings = []

    # 0. Фронтматтер SKILL.md. Без него навык не подхватится платформой, а
    # текст при этом выглядит совершенно нормальным — глазами не ловится.
    fm_ok = skill_text.lstrip().startswith("---") and skill_text.lstrip().count("---") >= 2
    if not fm_ok:
        findings.append(finding(
            "SKILL_FRONTMATTER_MISSING", BLOCKING,
            "В SKILL.md нет YAML-фронтматтера",
            "Добавь в начало файла блок между --- с полями name, summary, "
            "version. Без него навык не опознаётся платформой, хотя текст "
            "выглядит целым.",
            where={"file": "SKILL.md"}))
    else:
        fm = skill_text.lstrip().split("---", 2)[1]
        miss = [k for k in ("name:", "summary:", "version:") if k not in fm]
        if miss:
            findings.append(finding(
                "SKILL_FRONTMATTER_INCOMPLETE", BLOCKING,
                "Во фронтматтере SKILL.md не хватает полей",
                f"Добавь: {', '.join(miss)}",
                where={"file": "SKILL.md", "missing": miss}))

    # 0б. Команд вообще нет. Кит без команд разворачивается «успешно», а на
    # демонстрации у агента не оказывается ни одной точки входа.
    cmd_headers = [l for l in cmd_text.splitlines() if re.match(r"^#{2,6}\s+\S", l.strip())]
    if not cmd_headers:
        findings.append(finding(
            "NO_COMMANDS_DEFINED", BLOCKING,
            "В файле команд нет ни одной команды (заголовков ## не найдено)",
            "Опиши каждую команду заголовком уровня ## с префиксом номера "
            "агента. Пустой или бесструктурный файл команд означает агента "
            "без точек входа — на демонстрации это видно сразу.",
            where={"file": "03_Команды"}))

    if not SLUG_RE.match(slug):
        findings.append(finding(
            "SLUG_NOT_KEBAB", BLOCKING,
            f"slug '{slug}' не в kebab-case",
            "Приведи slug к виду latin-lowercase-with-hyphens: он попадает в "
            "имя навыка и имя папки build-kit.",
            where={"slug": slug}))

    # 1. SKILL.md — источник истины, полнота разделов.
    # Ищем разделы среди markdown-заголовков, а не по всему тексту: файл, где
    # названия разделов перечислены в оглавлении или упомянуты в прозе, но
    # самих разделов нет, иначе проходит проверку как полный.
    headers = " \n".join(re.findall(r"^#{1,4}\s*(.+)$", skill_text, re.M)).lower()
    missing_sections = [s for s in REQUIRED_SECTIONS if s.lower() not in headers]
    if missing_sections:
        findings.append(finding(
            "SKILL_SECTIONS_MISSING", BLOCKING,
            f"В SKILL.md не найдено разделов: {len(missing_sections)}",
            "Добавь недостающие разделы по шаблону agent_package.md. Пустой "
            "раздел лучше отсутствующего: он показывает, что вопрос рассмотрен.",
            where={"sections": missing_sections},
            expected=len(REQUIRED_SECTIONS), actual=len(REQUIRED_SECTIONS) - len(missing_sections)))

    # 2. Имя навыка — не константа 00-agent-skill в портфельном режиме
    expected_name = f"{agent_number}_{slug}" if agent_number else slug
    if "name: 00-agent-skill" in skill_text and agent_number:
        findings.append(finding(
            "SKILL_NAME_COLLISION_RISK", BLOCKING,
            "Имя навыка — константа '00-agent-skill' из шаблона одиночного агента",
            f"Замени на '{expected_name}'. В пространстве с несколькими агентами "
            "константа даёт коллизию имён навыков.",
            where={"file": "SKILL.md"}, expected=expected_name, actual="00-agent-skill"))

    # 3. Блок активации навыка — в начале ТЕКСТА ПОЛЯ «Инструкция», а не файла.
    # Тонкость, на которой легко ошибиться в обе стороны: файл карточки обычно
    # начинается с полей Название и Описание (это отдельные поля формы), и
    # требовать активацию первой строкой файла — значит браковать корректный
    # кит. Но и окно «первые N символов от начала файла» не годится: на
    # коротком тексте оно вмещает весь документ, и блок, уехавший в конец,
    # проходит. Правильная единица — раздел «Инструкция», если он размечен.
    # Заголовок раздела может нести пояснение: «## Инструкция (для поля
    # «Инструкции» — переносится целиком)», «### Инструкция агента». Требовать
    # голое слово — значит ложно валить корректный кит: найдено на боевом
    # прогоне, где исполнитель написал заголовок с пояснением в скобках и
    # обошёл проверку только потому, что заранее прочитал исходник скрипта.
    m = re.search(r"^#{1,6}\s*Инструкц\w*\b.*$", instr_text, re.M | re.I)
    scope = instr_text[m.end():] if m else instr_text
    meaningful = [l.strip().lower() for l in scope.splitlines()
                  if l.strip() and not l.strip().startswith("#")]
    head = " ".join(meaningful[:4])
    missing_activation = [p for p in ACTIVATION_PHRASES if p not in head]
    if missing_activation:
        anywhere = [p for p in ACTIVATION_PHRASES if p in instr_text.lower()]
        hint = ("Вставь обе фразы буквально, в начало текста, до идентичности "
                "агента (agent_card_kit.md). Без них агент в части сессий не "
                "подхватывает навык, и результат у разных пользователей "
                "отличается кратно.")
        if anywhere:
            hint = ("Фразы активации в тексте есть, но не в начале — перенеси их "
                    "в самое начало поля, до всего остального. Требование именно "
                    "к порядку: агент читает поле сверху вниз, и активация ниже "
                    "по тексту срабатывает не всегда. " + hint)
        findings.append(finding(
            "ACTIVATION_BLOCK_MISSING", BLOCKING,
            "В начале поля «Инструкция» нет полного блока активации навыка",
            hint,
            where={"file": "02_Инструкция", "missing": missing_activation,
                   "найдено_ниже_по_тексту": bool(anywhere)}))

    # 3б. Порядок нумерованных разделов SKILL.md — монотонный?
    nums = [int(n) for n in re.findall(r"^#{1,4}\s*(\d{1,2})\.\s", skill_text, re.M)]
    if nums and nums != sorted(nums):
        findings.append(finding(
            "SECTION_ORDER_BROKEN", WARNING,
            "Нумерованные разделы SKILL.md идут не по порядку",
            "Расставь разделы по возрастанию номеров. Содержание при этом не "
            "теряется, но читающий агент ориентируется по порядку, а докс "
            "собирается из разделов по номеру.",
            where={"order": nums}))

    # 4. Согласованность чисел между SKILL.md и производными полями
    skill_numbers = extract_numbers(skill_text)
    for label, text in (("01_Агент", card_text), ("02_Инструкция", instr_text)):
        drifted = extract_numbers(text) - skill_numbers
        if drifted:
            findings.append(finding(
                "NUMBER_DRIFT", WARNING,
                f"{label}: числа, не встречающиеся в SKILL.md",
                "Сверь глазами: либо число устарело в производном файле, либо "
                "SKILL.md отстал от него. Возможно ложное срабатывание (даты, "
                "номера версий) — это WARNING, не блокировка.",
                where={"file": label, "numbers": sorted(drifted)}))

    # 5. Команды ссылаются на реально существующие режимы.
    # Сначала — не прошла ли проверка вхолостую: если конструкции [РЕЖИМ ...]
    # в тексте есть, а распознать не удалось ни одной, сравнение множеств
    # даст «расхождений нет» при любом реальном рассинхроне.
    instr_modes, cmd_modes = extract_modes(instr_text), extract_modes(cmd_text)
    for label, text, parsed in (("02_Инструкция", instr_text, instr_modes),
                                ("03_Команды", cmd_text, cmd_modes)):
        if looks_like_modes(text) and not parsed:
            findings.append(finding(
                "MODE_SYNTAX_UNPARSED", BLOCKING,
                f"{label}: конструкции [РЕЖИМ …] есть, но ни одна не распознана",
                "Приведи оформление к виду [РЕЖИМ «Название»] — «ёлочки» или "
                "прямые кавычки, без лишних пробелов перед закрывающей скобкой. "
                "Пока названия не читаются, сверка команд с режимами проходит "
                "вхолостую и ничего не гарантирует.",
                where={"file": label}))

    dangling = cmd_modes - instr_modes
    if dangling:
        findings.append(finding(
            "COMMAND_MODE_DANGLING", BLOCKING,
            "Команды ссылаются на режимы, отсутствующие в тексте инструкции",
            "Приведи названия режимов к одному написанию либо добавь "
            "недостающий режим в 02_Инструкция. Типовая причина — режим "
            "переименовали в одном файле и не переименовали в другом.",
            where={"file": "03_Команды", "modes": sorted(dangling)}))

    # 5б. Файлы, на которые ссылаются команды, существуют в базе знаний.
    # Это самая частая причина провала демонстрации: команда говорит «найди
    # в базе знаний файл X», агент не находит и отвечает общими словами —
    # при клиенте, в реальном времени. Проверять имеет смысл только когда
    # база знаний передана явно (--kb).
    refs = {nfc(x) for x in FILE_REF_RE.findall(cmd_text)}
    if kb_dir and refs:
        have_rel, have_base = set(), set()
        for root, _, files in os.walk(kb_dir):
            for f in files:
                rel = nfc(os.path.relpath(os.path.join(root, f), kb_dir))
                have_rel.add(rel)
                have_base.add(nfc(f))
        missing = sorted(r for r in refs
                         if r not in have_rel and nfc(os.path.basename(r)) not in have_base)
        if missing:
            findings.append(finding(
                "COMMAND_FILE_REF_MISSING", BLOCKING,
                "Команды ссылаются на файлы, которых нет в базе знаний агента",
                "Либо загрузи недостающие файлы в базу знаний, либо поправь "
                "имена в командах. Ненайденный файл на демонстрации выглядит "
                "как «агент не работает», а не как «файл забыли положить».",
                where={"kb": str(kb_dir), "missing": missing[:8]},
                expected=len(refs), actual=len(refs) - len(missing)))

    # 6. Префикс номера агента в названиях команд (только портфельный режим).
    # Считаем командами только заголовки ## и глубже: H1 — это название
    # самого документа («Слэш-команды — Агент № 2, …»), у него префикса нет
    # и быть не должно. Проверка, спотыкающаяся о заголовок файла, срабатывала
    # бы на каждом корректно собранном ките — и её бы перестали слушать.
    if agent_number:
        cmd_lines = [l for l in cmd_text.splitlines()
                     if re.match(r"^#{2,6}\s+\S", l.strip())]
        unprefixed = [l.strip() for l in cmd_lines
                      if not re.search(rf"(?<!\d){re.escape(agent_number)}\.\d+", l)]
        if unprefixed:
            findings.append(finding(
                "COMMAND_PREFIX_MISSING", BLOCKING,
                f"Заголовки команд без префикса '{agent_number}.N'",
                f"Добавь префикс '{agent_number}.N' в начало названия каждой команды: "
                "пул команд пространства плоский, без префикса команды разных "
                "агентов неразличимы в общем списке.",
                where={"file": "03_Команды", "lines": unprefixed[:5]}))

    blocking = [f for f in findings if f["severity"] == BLOCKING]
    return {
        "status": "FAIL" if blocking else "PASS",
        "findings": findings,
        "blocking": blocking,
        "missing_sections": missing_sections,
    }


def render_findings(findings) -> str:
    if not findings:
        return "findings: []"
    out = ["findings:"]
    for f in findings:
        out.append(f"  - id: {f['id']}")
        out.append(f"    severity: {f['severity']}")
        out.append(f"    message: {f['message']}")
        out.append(f"    fix_hint: {f['fix_hint']}")
        for key in ("where", "expected", "actual"):
            if key in f:
                out.append(f"    {key}: {f[key]}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--agent-number", default=None, help="только для агента внутри портфеля, например '2'")
    ap.add_argument("--skill", required=True, type=Path)
    ap.add_argument("--card", required=True, type=Path, help="01_Агент_<slug>.md")
    ap.add_argument("--instruction", required=True, type=Path, help="02_Инструкция_<slug>.md")
    ap.add_argument("--commands", required=True, type=Path, help="03_Команды_<slug>.md")
    ap.add_argument("--build-guide", type=Path, default=None, help="00_Инструкция_по_сборке.md, опционально")
    ap.add_argument("--spec-docx", type=Path, default=None, help="04_Спецификация_<slug>.docx, опционально")
    ap.add_argument("--kb", type=Path, default=None,
                    help="папка базы знаний агента — включает проверку, что "
                         "файлы, упомянутые в командах, там действительно есть")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--work", type=Path, default=DEFAULT_WORK,
                    help=f"временная папка сборки (по умолчанию {DEFAULT_WORK})")
    args = ap.parse_args()

    skill_text = read(args.skill, "skill")
    card_text = read(args.card, "card")
    instr_text = read(args.instruction, "instruction")
    cmd_text = read(args.commands, "commands")

    result = audit(skill_text, card_text, instr_text, cmd_text, args.slug,
                   args.agent_number, args.kb)

    print("consistency_status: " + result["status"])
    print(f"agent_slug: {args.slug}")
    print(f"agent_number: {args.agent_number or '(одиночный, вне портфеля)'}")
    print(f"blocking: {len(result['blocking'])}  "
          f"warning: {len(result['findings']) - len(result['blocking'])}")
    print(render_findings(result["findings"]))

    # При BLOCKING build-kit физически не формируется. Предупреждение в stderr
    # с последующей записью архива — это ложный шлюз: рядом с готовым файлом
    # предупреждение перестают читать, и в перенос уходит несогласованный кит.
    if result["status"] == "FAIL":
        print("\nBuild-kit НЕ собран: устрани BLOCKING-нарушения по fix_hint и "
              "запусти повторно.", file=sys.stderr)
        return EXIT_AUDIT_FAIL

    prefix = f"{args.agent_number}_{args.slug}" if args.agent_number else args.slug
    root = args.work / prefix
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    (root / "SKILL.md").write_text(skill_text, encoding="utf-8")
    # Карточка и инструкция — ОДИН файл, как на боевом портфеле. Если оба
    # аргумента указывают на один путь (штатный вызов), второй файл не
    # создаётся: раньше скрипт писал два файла с идентичным содержимым и
    # сдвигал нумерацию команд на 03, расходясь с agent_card_kit.md. На
    # боевом тесте исполнитель прочитал это как «скрипт требует раздельные
    # файлы» и не собрал ни одного из семи пакетов.
    (root / f"01_Агент_{args.slug}.md").write_text(card_text, encoding="utf-8")
    same = card_text == instr_text
    if not same:
        (root / f"01б_Инструкция_{args.slug}.md").write_text(instr_text, encoding="utf-8")
    (root / f"02_Команды_{args.slug}.md").write_text(cmd_text, encoding="utf-8")
    if args.build_guide and args.build_guide.exists():
        shutil.copy(args.build_guide, root / "00_Инструкция_по_сборке.md")
    if args.spec_docx and args.spec_docx.exists():
        shutil.copy(args.spec_docx, root / f"03_Спецификация_{args.slug}.docx")

    args.out.mkdir(parents=True, exist_ok=True)
    zip_path = args.out / f"{prefix}.zip"
    # Детерминированный архив: фиксированные метки времени, чтобы SHA-256
    # не менялся на каждой пересборке того же содержимого.
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(
                path.relative_to(root.parent).as_posix(), date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())

    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"\nzip_path: {zip_path}")
    print(f"sha256: {digest}")
    print("note: этот ZIP не грузится в платформу как файл агента — это "
          "контейнер для ручного переноса полей (agent_card_kit.md)")
    print("deployed_state_check: требуется выполнить вручную от лица "
          "не-администраторского аккаунта после переноса полей в конструктор — "
          "это действие ЧЕЛОВЕКА, скрипт его не выполняет и не может подтвердить")

    return 0


if __name__ == "__main__":
    sys.exit(main())
