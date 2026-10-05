#!/usr/bin/env python3
"""
build_user_package.py — пакет агента на пути обычного пользователя: архив
проверки черновика и выпуск.

Путь — `references/pipeline_user_path.md`. Агента в GigaCowork собирает сам
пользователь: Фабрика в пространство не пишет, а чаты не видят файлов друг
друга. Поэтому выпуск — пакет, разложенный по шагам сборки, а черновик для
контрольной проверки в новом чате — один архив с шапкой для модели.

    python3 build_user_package.py --проект <папка проекта> --проверка <N>
    python3 build_user_package.py --проект <папка проекта> --выпуск <N> \\
        [--уровень Пилот] [--проверено "6 тендеров, 6 из 6"]

Необязательно: `--spec` (по умолчанию `<проект>/AGENT_SPEC.md`), `--kb`
(папка базы знаний; по умолчанию `<проект>/../база_знаний`, при повторном
выпуске версии — её сохранённая копия), `--out` (по умолчанию папка над
проектом), `--date`.

**Проверка.** «Проверка — <агент>, черновик N.zip»: база знаний, навык,
команда и карточка — те же файлы, что уйдут в выпуск, — и шапка
`ПРОВЕРКА.md`: работать строго по инструкции, не додумывать, отмечать
пробелы. Код черновика считается из содержимого, поэтому по первой строке
ответа видно, какой файл проверяли; черновик записывается в
`<проект>/ЧЕРНОВИКИ.md`.

**Выпуск.** «Пакет — <агент>, версия N» (папка и zip): `0_Как_собрать_агента.md`,
`1_База знаний <агент>/`, `2_Навык.md`, `3_Команда.md`,
`4_Карточка_агента.md`, `5_Приёмка.md`; «Паспорт — <агент>, версия N.md»;
«Проект — <агент>.zip». Версия сохраняется в `<проект>/версии/V<N>/` вместе
с базой знаний — из неё прошлая версия выдаётся заново («Вернуть прошлую
версию»). Выпущенная версия не переписывается: тот же номер с другой
спецификацией не собирается.

**Ворота.** Ниже порога demo не собирается ничего; уровни «Пилот» и
«В работу» требуют порога pilot. Навык до записи проходит структурный аудит
пакета (`check_package.py`) в раскладке каталога.

Код возврата: 0 — собрано; 1 — ворота или аудит красные; 2 — ошибка входа.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factory_common import (  # noqa: E402
    EXIT_INPUT, EXIT_OK, EXIT_RED, Spec, cases, connectors, hitl_rows, io_lists, nfc,
    parse_spec, scope_lists, workspace_skills,
)
from build_agent_package import ACCESS_TEXT, render_command, render_skill  # noqa: E402
from build_delivery import _summary, activation_text  # noqa: E402
from check_package import audit_package, render as render_audit  # noqa: E402
from spec_readiness import check as readiness_check, render as render_readiness  # noqa: E402
import skill_header  # noqa: E402

ZIP_TS = (2020, 1, 1, 0, 0, 0)
SKIP = {"__pycache__", ".DS_Store"}

# Уровни агента. Те же слова — в `pipeline_user_path.md` и в паспорте:
# «пилот» везде значит одно и то же (selftest сверяет дословно).
УРОВНИ = {
    "Показать": ("Посмотреть, как агент справляется, и показать коллегам. Работает только на "
                 "тестовых примерах, в реальную работу не ставится.",
                 "3 примера или учебные случаи", "demo"),
    "Под присмотром": ("Агент помогает в реальной работе, но каждое его решение утверждаете вы.",
                       "примеры и проверка безопасности", "demo"),
    "Пилот": ("2–4 недели на реальных задачах: обычные случаи агент решает сам, спорные передаёт "
              "человеку. Измеряем пользу.",
              "5–7 настоящих примеров, из них 2 контрольных; получены все согласования по "
              "правилам компании", "pilot"),
    "В работу": ("Постоянный инструмент: есть владелец, ошибки разбираются, при смене модели "
                 "агент перепроверяется.", "пилот показал пользу", "pilot"),
}
КОД_АЛФАВИТ = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# --- имена -------------------------------------------------------------------
def agent_name(spec: Spec) -> str:
    return nfc(spec.fm("имя агента") or spec.slug)


def file_safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', " ", s).strip()


def command_name(spec: Spec) -> str:
    """Имя команды пространства: имя агента строчными через дефис («разбор-тендера»)."""
    n = re.sub(r"[^a-zа-яё0-9]+", "-", agent_name(spec).lower()).strip("-")
    return n or spec.slug


def kb_dir_name(spec: Spec) -> str:
    # У папки каждого агента своё имя: базы знаний разных агентов в одних
    # документах пространства не смешиваются (platform_architecture.md).
    return f"1_База знаний {file_safe(agent_name(spec))}"


def draft_code(parts: dict) -> str:
    h = hashlib.sha256()
    for k in sorted(parts):
        h.update(k.encode("utf-8") + b"\0" + parts[k] + b"\0")
    n = int.from_bytes(h.digest()[:4], "big")
    return "".join(КОД_АЛФАВИТ[(n >> (5 * i)) & 31] for i in range(4))


# --- тексты пакета -----------------------------------------------------------
def render_user_command(spec: Spec) -> str:
    name = agent_name(spec)
    trig = (spec.block(5).field_value("Триггер") or name).strip().rstrip(".")
    desc = skill_header.plain_line(f"Запуск агента «{name}» — {trig[:1].lower() + trig[1:]}", 200)
    return (f"---\nname: {command_name(spec)}\ndescription: {desc}\n---\n\n"
            f"Активируй навык «{name}» и выполни полный порядок выполнения из него.\n\n"
            "Задача: ${задача}\n\n"
            "Входные файлы — приложенные к сообщению; если их нет и источник не назван — спроси.\n")


def what_it_does(spec: Spec) -> str:
    """Что делает агент — одной строкой из периметра (in-scope), а не из
    описания возможности: там контекст процесса, а не работа агента."""
    inside, _ = scope_lists(spec.block(7))
    t = "; ".join(inside[:3]) or _summary(spec.block(1).text, 1) or agent_name(spec)
    return skill_header.plain_line(t[:1].upper() + t[1:], 300)


def company_rules(spec: Spec) -> str:
    """Запасной текст правил компании — из блока 11 без допущений и кода."""
    out = []
    for x in spec.block(11).bullets():
        if re.match(r"^(Допущение|Не знаем)\s*:", x):
            continue
        x = re.sub(r"\s*\(`[^`]*`\)", "", x).replace("`", "")
        out.append(f"- {x}")
    return "\n".join(out)


def owner(spec: Spec) -> str:
    b = spec.block(15)
    return b.field_value("Владелец") or b.field_value("Владелец процесса") or "не назначен"


def _hitl_points(spec: Spec) -> list:
    return [f"{r[0]} — {r[1]}" for r in hitl_rows(spec.block(10)) if r[0]]


def render_user_card(spec: Spec, version: str, today: str) -> str:
    name = agent_name(spec)
    b1, b11 = spec.block(1), spec.block(11)
    inside, outside = scope_lists(spec.block(7))
    ws, conns = workspace_skills(b11), connectors(b11)
    access = spec.fm("уровень доступа") or "L0"
    desc = what_it_does(spec)
    lst = lambda xs: "\n".join(f"- {x}" for x in xs) if xs else "- —"
    lines = ["---", f"name: {name}", f"description: {desc}", f"skills: [{spec.slug}]", "---", "",
             f"# Карточка агента — {name}", "",
             f"Значения полей формы «Создание агента». Переносите дословно. Версия {version} · {today}.", "",
             "## Название", "", name, "", "## Описание", "", desc, "",
             "## Инструкция", "", activation_text(spec), "",
             f"**Роль и цель.** {_summary(b1.text, 2)} Агент не заменяет решение человека: "
             "результат проверяет и принимает пользователь.", "",
             "**Что делает:**", lst(inside), "", "**Чего не делает:**", lst(outside),
             "Запрос за периметром — назвать границу и сказать, что агент может сделать вместо этого.", "",
             "**Где решает человек:**", lst(_hitl_points(spec)), "",
             f"**Порядок работы, правила и форма результата** — в навыке «{name}»; здесь они не "
             "пересказываются, чтобы не разошлись.", "",
             f"**Доступ.** Уровень {access}: {ACCESS_TEXT.get(access, '—')}.", "",
             "## Навыки", "",
             f"- «{name}» — свой навык агента: отметьте его в поле навыков, сам он не подключится."]
    lines += [f"- «{w['name']}» — навык пространства: должен стоять в пространстве до создания агента."
              for w in ws]
    lines += ["- Системные навыки — работа с файлами, Excel, пространство знаний — в новой карточке "
              "уже есть, их оставьте как есть.", "",
              "## Команда", "", f"Привяжите команду «{command_name(spec)}» из файла «3_Команда».", "",
              "## Коннекторы", ""]
    lines += ([f"- {c['name']} — {c['what']}" + (f" ({c['version']})" if c["version"] else "") for c in conns]
              or ["Не нужны: агент работает только с файлами, которые прикладывает пользователь."])
    return "\n".join(lines) + "\n"


def render_kb_readme(spec: Spec, names: list) -> str:
    name = agent_name(spec)
    return "\n".join([
        f"# Что здесь и чего нет — {name}", "",
        "Папка загружается в документы пространства целиком, ничего не разбирая. Агент читает её "
        "только на чтение; программы запускает отсюда, результат пишет в рабочую папку чата.", "",
        "## Что здесь", "", *([f"- `{n}`" for n in names] or ["- файлов, кроме этого, нет: агенту "
                                                              "хватает навыка и файлов пользователя"]), "",
        "## Чего здесь нет", "",
        "- рабочих данных пользователя: их прикладывают к сообщению в чате;",
        "- правил, которые не подписаны владельцем: такие остаются вопросами в паспорте агента.", ""])


def render_acceptance(spec: Spec, version: str) -> str:
    name, cmd = agent_name(spec), command_name(spec)
    trig = (spec.block(5).field_value("Триггер") or "").strip().rstrip(".")
    _, outside = scope_lists(spec.block(7))
    rows = [("Обычной фразой, без имени навыка и без команды, опишите задачу, как коллеге"
             + (f" (повод — {trig})" if trig else "") + ", и приложите контрольный пример",
             "Агент сам берёт свой навык; результат совпадает с вашей проверкой примера"),
            ("Тот же вход ещё раз, в новом чате",
             "Тот же результат: состав и выводы совпадают, формулировки могут отличаться")]
    for c in cases(spec.block(12)):
        if c["id"] in ("happy_path", "same_input_twice"):
            continue
        rows.append((c["input"] or c["id"], c["expect"] or "—"))
    if outside:
        rows.append((f"Чужая задача: «{outside[0]}»",
                     "Не берётся: говорит, что это не его задача, и что он умеет"))
    esc = lambda t: t.replace("|", "/")
    out = [f"# Приёмка — {name}, версия {version.lstrip('V')}", "",
           f"Где: новый чат с агентом «{name}» в пространстве. Ответы агента вставьте в чат Фабрики "
           "целиком, без пересказа: оценку Фабрика ставит по признакам.", "",
           "## 0. Видимость", "",
           f"Коллега без прав администратора открывает агента: он видит агента, навык «{name}» и "
           f"команду «{cmd}» так же, как вы.", "",
           "## Проверки", "", "| № | Что отправить | Что должно быть в ответе |", "|---|---|---|"]
    out += [f"| {i} | {esc(a)} | {esc(b)} |" for i, (a, b) in enumerate(rows, 1)]
    return "\n".join(out) + "\n"


def render_guide(spec: Spec, version: str) -> str:
    name, cmd = agent_name(spec), command_name(spec)
    ws, conns = workspace_skills(spec.block(11)), connectors(spec.block(11))
    cat = skill_header.category_value(spec.fm("категория"))
    conn_line = ("Подключите коннекторы из раздела «Коннекторы» карточки." if conns
                 else "Коннекторы этому агенту не нужны.")
    ws_line = (" До этого в пространстве должны стоять навыки: "
               + ", ".join(f"«{w['name']}»" for w in ws) + "." if ws else "")
    return "\n".join([
        f"# Как собрать агента «{name}» — версия {version.lstrip('V')}", "",
        "Агента в GigaCowork собираете вы, за пять шагов. Файлы пакета пронумерованы по шагам. "
        "Фабрика в пространство не пишет: она выдаёт файлы, загружаете их вы.", "",
        f"1. **Документы.** Загрузите папку «{kb_dir_name(spec)}» в документы пространства целиком, "
        "ничего не разбирая.",
        f"2. **Навык.** В навыках пространства загрузите файл «2_Навык». Имя, поле «Когда применять» "
        f"и инструкция заполнятся из файла сами. Категорию выберите в списке вручную: «{cat}». "
        f"Сверьте имя: «{name}».{ws_line}",
        f"3. **Команда.** Создайте команду «{cmd}»: имя, описание и текст скопируйте из файла «3_Команда».",
        "4. **Карточка агента.** Создайте агента: название, описание и инструкцию скопируйте из файла "
        "«4_Карточка_агента». Инструкция начинается с включения навыка — не сокращайте её. В поле "
        f"навыков отметьте «{name}»: сам он не подключится. Системные навыки — работа с файлами, Excel, "
        f"пространство знаний — в новой карточке уже есть, их оставьте как есть. Привяжите команду. "
        f"{conn_line}",
        "5. **Проверка.** Попросите коллегу без прав администратора открыть агента, затем в новом чате с "
        "агентом пройдите проверки из файла «5_Приёмка». Ответы вставьте в чат Фабрики целиком.", "",
        "## Если что-то не так", "",
        "- Агент отвечает «вообще», не по правилам — в карточке не отмечен свой навык или инструкция "
        "сокращена.",
        "- Агент не находит файлы базы знаний — папка загружена не целиком или переименована.",
        "- Коллега не видит агента — проверьте доступ к пространству.", "",
        "## Следующая версия", "",
        "Фабрика скажет, что заменить: обычно файл навыка (шаг 2), иногда папку базы знаний (шаг 1). "
        "Прошлая версия хранится в файле проекта: попросите Фабрику «Вернуть прошлую версию».", ""])


def render_passport(spec: Spec, version: str, today: str, level: str, checked: str,
                    rules_text: str, versions: list) -> str:
    name, cmd = agent_name(spec), command_name(spec)
    inside, outside = scope_lists(spec.block(7))
    access = spec.fm("уровень доступа") or "L0"
    never = list(outside)
    if access in ("L0", "L1", "L2"):
        never.append("записывать во внешние системы")
    hitl = _hitl_points(spec)
    trig = (spec.block(5).field_value("Триггер") or "").strip().rstrip(".")
    n = max(len(inside), len(hitl), len(never), 1)
    cell = lambda xs, i: xs[i].replace("|", "/") if i < len(xs) else ""
    rows = [f"| {cell(inside, i)} | {cell(hitl, i)} | {cell(never, i)} |" for i in range(n)]
    lv = [f"| {k}{' ← этот агент' if k == level else ''} | {v[0]} | {v[1]} |" for k, v in УРОВНИ.items()]
    return "\n".join([
        f"# Паспорт агента — {name}", "",
        f"Версия {version.lstrip('V')} · {today} · уровень «{level}»", "",
        f"**{level}.** {УРОВНИ[level][0]}", "",
        "## Что делает", "", what_it_does(spec) + ".", "",
        "## Как запускать", "",
        f"В чате с агентом — обычной фразой{(' (повод — ' + trig + ')') if trig else ''} или командой «{cmd}». "
        "Файлы прикладывайте к сообщению.", "",
        "## Сам, с вашим подтверждением, никогда", "",
        "| Сам | С вашим подтверждением | Никогда |", "|---|---|---|", *rows, "",
        "## Правила компании", "", rules_text.strip() or "—", "",
        "## На чём проверен", "", checked or "Не указано.", "",
        "## Уровни агента", "", "| Уровень | Что это | Что нужно |", "|---|---|---|", *lv, "",
        "## Владелец", "", owner(spec), "",
        "## Версии", "", *[f"- {v}" for v in versions], ""])


# --- файлы -------------------------------------------------------------------
def kb_files(kb: Path | None) -> dict:
    out = {}
    if kb is None or not kb.is_dir():
        return out
    for p in sorted(kb.rglob("*")):
        rel = p.relative_to(kb)
        if p.is_file() and not any(x in SKIP for x in rel.parts) and p.suffix != ".pyc":
            out[rel.as_posix()] = p.read_bytes()
    return out


def package_parts(spec: Spec, version: str, today: str, kb: dict) -> dict:
    """База знаний, навык, команда, карточка — общие для проверки и выпуска."""
    kbn = kb_dir_name(spec)
    parts = {f"{kbn}/{k}": v for k, v in kb.items()}
    readme = f"{kbn}/00_Что_здесь_и_чего_нет.md"
    if readme not in parts:
        parts[readme] = render_kb_readme(spec, sorted(kb)).encode("utf-8")
    parts["2_Навык.md"] = render_skill(spec, version, today).encode("utf-8")
    parts["3_Команда.md"] = render_user_command(spec).encode("utf-8")
    parts["4_Карточка_агента.md"] = render_user_card(spec, version, today).encode("utf-8")
    return parts


def write_zip(path: Path, parts: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for k in sorted(parts):
            zi = zipfile.ZipInfo(k, date_time=ZIP_TS)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.flag_bits |= 0x800
            z.writestr(zi, parts[k])
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_tree(root: Path, parts: dict) -> None:
    if root.exists():
        shutil.rmtree(root)
    for k, v in parts.items():
        (root / k).parent.mkdir(parents=True, exist_ok=True)
        (root / k).write_bytes(v)


def audit_skill(spec: Spec, version: str, today: str) -> dict:
    """Навык проходит тот же структурный аудит, что пакет консультанта."""
    with tempfile.TemporaryDirectory() as t:
        pkg = Path(t) / spec.slug
        (pkg / "skills" / "00-agent-skill").mkdir(parents=True)
        (pkg / "commands").mkdir()
        (pkg / "skills" / "00-agent-skill" / "SKILL.md").write_text(render_skill(spec, version, today),
                                                                     encoding="utf-8")
        (pkg / "commands" / "run-agent.md").write_text(render_command(spec, version, today), encoding="utf-8")
        return audit_package(pkg, spec.path)


def instruction_body(skill: str) -> str:
    return skill.split("\n---\n", 1)[1].lstrip("\n") if skill.startswith("---\n") else skill


# --- режимы ------------------------------------------------------------------
def do_check(spec: Spec, proj: Path, out: Path, n: int, version: str, today: str, kb: dict) -> int:
    name = agent_name(spec)
    parts = package_parts(spec, version, today, kb)
    code = draft_code(parts)
    skill = parts["2_Навык.md"].decode("utf-8")
    title = f"Проверка — {file_safe(name)}, черновик {n}"
    head = "\n".join([
        f"ПРОВЕРКА ЧЕРНОВИКА · «{name}» · черновик {n} · код {code}", "",
        "Для модели. Это проверка черновика агента, а не рабочая задача Фабрики.",
        f"1. Ниже — инструкция агента «{name}» (та же, что в файле 2_Навык.md). До конца этого чата "
        "работай строго по ней, как этот агент.",
        "2. Используй только эту инструкцию, файлы архива и файлы пользователя. Другие навыки не "
        "вызывай, инструкцию не улучшай.",
        "3. Если инструкция не говорит, как поступить, не додумывай: сделай, что можешь, и отметь "
        "«ПРОБЕЛ: …».",
        f"4. Архив распакован в рабочую папку (/session); база знаний — папка «{kb_dir_name(spec)}». "
        "Программы из неё запускай как есть, результаты пиши в /session.",
        "5. Вопросы пользователю задавай, только если этого требует инструкция.",
        f"6. Первая строка ответа: «Черновик {n} · {code}». Последний блок: «Для Фабрики: пробелы — …» "
        "или «Для Фабрики: пробелов нет».", "",
        "=== ИНСТРУКЦИЯ АГЕНТА ===", "", instruction_body(skill)])
    parts["ПРОВЕРКА.md"] = head.encode("utf-8")
    zp = out / f"{title}.zip"
    sha = write_zip(zp, parts)
    log = proj / "ЧЕРНОВИКИ.md"
    rows = log.read_text(encoding="utf-8") if log.exists() else (
        "# Черновики на контрольной проверке\n\nКод в первой строке ответа агента должен совпасть с "
        "последней строкой.\n\n| Черновик | Код | Спецификация | Дата |\n|---|---|---|---|\n")
    line = f"| {n} | {code} | {spec.fm('версия спецификации')} · {spec.fp} | {today} |\n"
    if line not in rows:
        log.write_text(rows + line, encoding="utf-8")
    print(f"Архив проверки: {zp}")
    print(f"Код черновика: Черновик {n} · {code} · SHA-256 {sha[:12]}")
    print("Фраза для нового чата без навыков (приложить архив и контрольный вход без ответа):")
    print(f"  Распакуй архив «{title}» в /session, прочитай ПРОВЕРКА.md и работай по нему. "
          "Задача — <контрольный пример>, файл приложен.")
    return EXIT_OK


def do_release(spec: Spec, proj: Path, out: Path, n: int, version: str, today: str, kb: dict,
               level: str, checked: str) -> int:
    name = agent_name(spec)
    vdir = proj / "версии" / version
    meta = vdir / "ВЫПУСК.json"
    if meta.exists():
        prev = json.loads(meta.read_text(encoding="utf-8"))
        if prev.get("спецификация") != spec.fp:
            print(f"ОШИБКА ВХОДА: версия {n} уже выпущена со спецификацией {prev.get('спецификация')}; "
                  f"выпущенная версия не переписывается — выпускайте {n + 1}.", file=sys.stderr)
            return EXIT_INPUT
    parts = package_parts(spec, version, today, kb)
    parts["0_Как_собрать_агента.md"] = render_guide(spec, version).encode("utf-8")
    parts["5_Приёмка.md"] = render_acceptance(spec, version).encode("utf-8")

    # версия в проекте — до упаковки проекта
    vdir.mkdir(parents=True, exist_ok=True)
    if spec.path.resolve() != (vdir / "AGENT_SPEC.md").resolve():
        shutil.copyfile(spec.path, vdir / "AGENT_SPEC.md")
    for k in ("2_Навык.md", "3_Команда.md", "4_Карточка_агента.md"):
        (vdir / k).write_bytes(parts[k])
    kb_store = vdir / "база_знаний"
    if not kb_store.exists():
        for k, v in kb.items():
            (kb_store / k).parent.mkdir(parents=True, exist_ok=True)
            (kb_store / k).write_bytes(v)
    if not meta.exists():
        meta.write_text(json.dumps({"версия": n, "спецификация": spec.fp, "дата": today, "уровень": level,
                                    "проверено": checked}, ensure_ascii=False, indent=2), encoding="utf-8")

    versions = []
    for v in sorted((proj / "версии").glob("V*/ВЫПУСК.json"), key=lambda p: int(p.parent.name[1:] or 0)):
        d = json.loads(v.read_text(encoding="utf-8"))
        versions.append(f"версия {d['версия']} · {d['дата']} · уровень «{d['уровень']}»"
                        + (" — выдана сейчас" if d["версия"] == n else ""))
    rules = proj / "Правила_компании.md"
    rules_text = rules.read_text(encoding="utf-8") if rules.exists() else company_rules(spec)

    pkg_title = f"Пакет — {file_safe(name)}, версия {n}"
    write_tree(out / pkg_title, parts)
    sha = write_zip(out / f"{pkg_title}.zip", parts)
    passport = out / f"Паспорт — {file_safe(name)}, версия {n}.md"
    passport.write_text(render_passport(spec, version, today, level, checked, rules_text, versions),
                        encoding="utf-8")
    proj_parts = {k: v for k, v in kb_files(proj).items()}
    proj_parts["ПРОЕКТ.md"] = (
        f"# Проект агента «{name}»\n\nОткрывать не нужно. Это файл проекта Фабрики: намерение, "
        "спецификация, примеры с замороженными ответами, прогоны, решения и все выпущенные версии. "
        "Приложите его к чату Фабрики, когда захотите улучшить агента или вернуть прошлую версию.\n"
    ).encode("utf-8")
    psha = write_zip(out / f"Проект — {file_safe(name)}.zip", proj_parts)
    print(f"Пакет: {out / (pkg_title + '.zip')} · SHA-256 {sha[:12]}")
    print(f"Паспорт: {passport}")
    print(f"Файл проекта: {out / ('Проект — ' + file_safe(name) + '.zip')} · SHA-256 {psha[:12]}")
    print("Скачайте все три файла из панели «Документы»: файлы сессии не переживают сессию.")
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Пакет агента на пути обычного пользователя")
    ap.add_argument("--проект", dest="proj", required=True, help="папка проекта агента")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--проверка", dest="draft", type=int, help="номер черновика для контрольной проверки")
    g.add_argument("--выпуск", dest="release", type=int, help="номер выпускаемой версии")
    ap.add_argument("--spec", default=None)
    ap.add_argument("--kb", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--уровень", dest="level", default="Показать", choices=list(УРОВНИ))
    ap.add_argument("--проверено", dest="checked", default="")
    ap.add_argument("--date", default=date.today().isoformat())
    a = ap.parse_args(argv)

    proj = Path(a.proj)
    if not proj.is_dir():
        print(f"ОШИБКА ВХОДА: нет папки проекта {proj}", file=sys.stderr)
        return EXIT_INPUT
    spec_path = Path(a.spec) if a.spec else proj / "AGENT_SPEC.md"
    if not spec_path.is_file():
        print(f"ОШИБКА ВХОДА: нет спецификации {spec_path}", file=sys.stderr)
        return EXIT_INPUT
    spec = parse_spec(spec_path)
    out = Path(a.out) if a.out else proj.resolve().parent

    if a.release is not None:
        n = a.release
        version = f"V{n}"
    else:
        n = a.draft
        done = [int(p.name[1:]) for p in (proj / "версии").glob("V*") if p.name[1:].isdigit()]
        version = f"V{max(done, default=0) + 1}"     # черновик той версии, которой станет
    if n < 1:
        print("ОШИБКА ВХОДА: номер — целое число от 1", file=sys.stderr)
        return EXIT_INPUT
    meta = proj / "версии" / version / "ВЫПУСК.json"
    if a.release is not None and meta.exists():
        # Повторная выдача выпущенной версии («Вернуть прошлую версию»): дата,
        # уровень и отчёт о проверке — из записи выпуска, и файлы совпадают
        # с выданными тогда байт в байт.
        prev = json.loads(meta.read_text(encoding="utf-8"))
        a.date, a.level, a.checked = prev["дата"], prev["уровень"], prev.get("проверено", "")
        print(f"Повторная выдача версии {n}: дата {a.date}, уровень «{a.level}» — из записи выпуска.")
    if a.release is not None and a.level != "Показать" and not a.checked.strip():
        print(f"ОШИБКА ВХОДА: уровень «{a.level}» выпускается только после контрольной проверки — "
              "укажите --проверено «на чём и с каким счётом».", file=sys.stderr)
        return EXIT_INPUT

    threshold = УРОВНИ[a.level][2] if a.release is not None else "demo"
    gate = readiness_check(spec, threshold)
    if gate["verdict"] != "PASS":
        print(render_readiness(gate))
        print(f"\nНЕ СОБРАНО: спецификация ниже порога {threshold}.")
        return EXIT_RED
    audit = audit_skill(spec, version, a.date)
    if audit["status"] != "PASS":
        print(render_audit(audit))
        print("\nНЕ СОБРАНО: навык не прошёл аудит пакета.")
        return EXIT_RED

    if a.kb:
        kb_path = Path(a.kb)
    elif a.release is not None and (proj / "версии" / version / "база_знаний").is_dir():
        kb_path = proj / "версии" / version / "база_знаний"
    else:
        kb_path = proj.resolve().parent / "база_знаний"
    if a.kb and not kb_path.is_dir():
        print(f"ОШИБКА ВХОДА: нет папки базы знаний {kb_path}", file=sys.stderr)
        return EXIT_INPUT
    kb = kb_files(kb_path)
    if a.release is not None:
        return do_release(spec, proj, out, n, version, a.date, kb, a.level, a.checked.strip())
    return do_check(spec, proj, out, n, version, a.date, kb)


if __name__ == "__main__":
    sys.exit(main())
