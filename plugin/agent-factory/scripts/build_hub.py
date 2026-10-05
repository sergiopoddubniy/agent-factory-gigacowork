#!/usr/bin/env python3
"""build_hub.py — витринная сборка плагина для платформы GigaCowork.

Из `plugin/agent-factory` собирает плагин в раскладке витрины (по образцу,
полученному от платформы): `plugin.yaml`, `agents/agent-factory.md`,
`commands/*.md`, `skills/agent-factory/…`, `agent.html`, `README.md` — и
архив с именами файлов в UTF-8 (средствами Python, а не сторонним
архиватором: чужой архиватор однажды превратил кириллические имена в
`#U041f…`).

Референсов в витрине нет — это требование платформы: папка `examples/`
и шаблоны из `references/agent_templates/` не переносятся (флаг
`--с-примерами` возвращает их), база шаблонов и копилки пусты. Агент на
платформе проектируется с нуля по спецификации и собирается скриптами.
Примеры накапливаются в открытом репозитории.

Обезличивание — по `docs/06_Политика_обезличивания.md`: класс A в
репозитории отсутствует изначально (это проверяет `hub_gate.py --мягко`
в CI); класс B (провенанс) вырезается здесь: даты `дд.мм.гггг`, короткие
даты рядом с псевдонимом клиента, шаблон адреса инстанса платформы.
Правила и иглы проверок не трогаются — внутри витринной сборки
`selftest.py` и `selftest_delivery.py` обязаны остаться зелёными, и сборка
это запускает сама.

    python3 scripts/build_hub.py --out dist [--version V3.5] [--no-check] [--стоп-слова файл]

Заголовки витрины — по стандарту каталога GigaCowork и контракту загрузки
навыка (`skill_header.py`): `name`, `description` одной plain-строкой без
триггеров (триггеры — в теле навыка, раздел «Когда использовать»),
`category` из семи, `version` целым числом, `tags` — slug. Сборка сама
проверяет заголовки plugin.yaml, навыка, агента и команд и отсутствие
`.pyc`/`__pycache__` в архиве; нарушение — красный код.

Раскладка навыка в витрине — по конвенции каталога: справочники в папке
`Референсы/` одним уровнем (пайплайны режимов — `Референсы/pipeline_*.md`,
шаблоны спецификации и прогона — `Референсы/*.template.md`), без вложенных
папок; данные скриптов — база шаблонов и копилка реестра — не в
справочниках, а рядом: `agent_templates/` и `knowledge/` («хранилище данных
в референсах» стандарт относит к анти-паттернам; писать на платформе всё
равно можно только в `/session`, см. `workdir.py`). Пути в тексте и в коде
переписываются при сборке (`relayout`), а сборка сама проверяет, что
`references/` в витрине не осталось и каждая ссылка на `Референсы/` ведёт к
файлу. Репозиторий остаётся в прежней раскладке: это мастерская консультанта.

Коды возврата: 0 — собрано и всё зелёное; 1 — шлюз или проверки красные;
2 — ошибка входа.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import os
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_header  # noqa: E402

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent                      # plugin/agent-factory
EXIT_OK, EXIT_RED, EXIT_INPUT = 0, 1, 2
TEXT_EXT = {".md", ".py", ".txt", ".yaml", ".yml", ".json", ".js", ".html"}
SKIP_DIRS = {"__pycache__", ".DS_Store", "hub"}
ZIP_TS = (2020, 1, 1, 0, 0, 0)

# --- вырезание провенанса (класс B) --------------------------------------
DATE_FULL = re.compile(r"(?:\s*(?:,|—|–|\()\s*)?(?:от\s+|за\s+)?\b\d{2}\.\d{2}\.20\d{2}\b(?:\s*г\.)?\)?")
DATE_SHORT_NEAR_ALIAS = re.compile(r"(Клиент-[А-Я][^\n]{0,60}?)(?:,\s*|\s+)\b\d{2}\.\d{2}\b(?!\.\d)")
DATE_SHORT_BEFORE_ALIAS = re.compile(r"\b\d{2}\.\d{2}\b(?!\.\d)(\s*[,—–:]?\s*[^\n]{0,60}?Клиент-[А-Я])")
VERSION_FOLDER_DATE = re.compile(r"(V\d+_)\d{2}\.\d{2}\.\d{2}\b")   # имя папки версии с датой в примерах
PLATFORM_URL = re.compile(r"https?://<инстанс>\.cowork\.ru")
PLATFORM_DOMAIN = re.compile(r"\bcowork\.ru\b")


CODE_LINE = re.compile(r"[\"']")   # строка кода со строковым литералом — не трогаем (фикстуры, регулярки)

# --- раскладка витрины: «Референсы/» одним уровнем, данные — рядом ----------
REFS_HUB = "Референсы"
DATA_DIRS = ("knowledge", "agent_templates")
RAW_FILES = {"hub_gate.py", "build_hub.py"}   # инструменты репозитория: копируются как есть
LAYOUT_RULES = [
    # данные скриптов выходят из справочников
    (re.compile(r'"references"\s*/\s*"(knowledge|agent_templates)"'), r'"\1"'),
    (re.compile(r"\breferences/(knowledge|agent_templates)(?![.\w])"), r"\1"),
    # шаблоны спецификации и прогона — в справочники
    (re.compile(r"\btemplates/(?=[^\s`)]*\.template\.md)"), REFS_HUB + "/"),
    (re.compile(r"шаблон в `templates/`"), f"шаблон в `{REFS_HUB}/`"),
    # сами справочники
    (re.compile(r"\breferences\b"), REFS_HUB),
]


def hub_rel(rel: Path) -> Path:
    """Путь файла скилла в витрине."""
    parts = rel.parts
    if parts and parts[0] == "references":
        if len(parts) > 2 and parts[1] in DATA_DIRS:
            return Path(*parts[1:])
        return Path(REFS_HUB, *parts[1:])
    if len(parts) == 2 and parts[0] == "templates" and parts[1].endswith(".template.md"):
        return Path(REFS_HUB, parts[1])
    return rel


def relayout(text: str) -> tuple[str, int]:
    n_total = 0
    for rx, repl in LAYOUT_RULES:
        text, n = rx.subn(repl, text)
        n_total += n
    return text, n_total


def _scrub_line(ln: str, code: bool, stats: dict) -> str:
    if code and CODE_LINE.search(ln):
        return ln
    t, n = DATE_FULL.subn("", ln)
    stats["даты"] += n
    t, n = DATE_SHORT_NEAR_ALIAS.subn(r"\1", t)
    stats["даты_у_псевдонима"] += n
    t, n = DATE_SHORT_BEFORE_ALIAS.subn(r"\1", t)
    stats["даты_у_псевдонима"] += n
    if not code:
        t, n = VERSION_FOLDER_DATE.subn(r"\1дд.мм.гг", t)
        stats["даты"] += n
    t, n = PLATFORM_URL.subn("https://<адрес-платформы>", t)
    stats["платформа"] += n
    t, n = PLATFORM_DOMAIN.subn("<домен-платформы>", t)
    stats["платформа"] += n
    if t != ln:
        # только в строке, где что-то вырезано: «Клиент-А .» → «Клиент-А.»
        t = re.sub(r"(?<=\S) ([.,;:])", r"\1", t)
    return t


def scrub(text: str, code: bool = False) -> tuple[str, dict]:
    """Провенанс вырезается построчно. В коде (.py/.js) строки со строковыми
    литералами не трогаются: там живут фикстуры проверок и регулярки, и
    «дата» в имени папки фикстуры — часть теста, а не провенанс."""
    stats = {"даты": 0, "даты_у_псевдонима": 0, "платформа": 0}
    t = "\n".join(_scrub_line(ln, code, stats) for ln in text.split("\n"))
    # Общих «чисток хвостов» здесь нет намеренно: правило вроде «убрать пустые
    # скобки» однажды превратило `def main() -> int:` в `def main -> int:`.
    return t, stats


# --- команды витрины -----------------------------------------------------------
# Витрина — Фабрика для обычного пользователя: три короткие команды с
# параметрами `${…}` из `templates/hub/commands/` (путь пользователя,
# `pipeline_user_path.md`). Девять команд консультанта остаются в
# репозитории и в папке навыка как сценарии для модели, но в каталог
# команд пространства не выносятся.
HUB_COMMANDS = ("new-agent.md", "improve-agent.md", "release-agent.md")
ARG = re.compile(r"\$\{([^}]*)\}")
CMD_BODY_MAX = 12       # строк тела: команда — короткий запуск, правила — в навыке


HUB_TEMPLATES_README = """# База шаблонов агентов — состав

**Происхождение:** синтетика либо обезличено. **Режим:** фикстура.

В этой сборке база пуста намеренно: агент собирается с нуля по
спецификации `AGENT_SPEC.md`, и готовые шаблоны для этого не нужны.
Правила базы и порядок попадания в неё — `references/agent_templates.md`.

Если база пополняется на месте, у каждого шаблона — роль, джоба и «когда
применять» во фронтматтере навыка и `КАРТА_ПЕРЕНОСА.md` в три графы:
заменить обязательно, проверить, не трогать. Индекс пересобирается
`python3 scripts/templates.py --индекс`, поиск —
`python3 scripts/templates.py --похожие "<джоба>"`. Честный ответ поиска на
пустой базе — «кандидатов не было», и он записывается в комплект (Я21).

## Чего здесь нет и не будет

Признаков принадлежности клиенту: названий юрлиц, ИНН и ОГРН, адресов,
фамилий, номеров договоров, сумм, по которым узнаётся клиент. Порогов и
правил клиента, перенесённых без замены: шаблон без карты переноса —
дефект базы.
"""


def _is_reference(rel: Path) -> bool:
    """Референс — пример собранного агента или шаблон в базе шаблонов."""
    if rel.parts and rel.parts[0] == "examples":
        return True
    return len(rel.parts) > 2 and rel.parts[0] == "references" and rel.parts[1] == "agent_templates" \
        and rel.parts[2] not in ("ИНДЕКС.md", "00_ЧТО_ЗДЕСЬ_И_ЧЕГО_ЗДЕСЬ_НЕТ.md")


def copy_skill(dst: Path, stats: dict, with_examples: bool = False) -> None:
    for p in sorted(PLUGIN.rglob("*")):
        rel = p.relative_to(PLUGIN)
        if any(part in SKIP_DIRS for part in rel.parts) or rel.parts[0] == "templates" and len(rel.parts) > 1 and rel.parts[1] == "hub":
            continue
        if not with_examples and _is_reference(rel):
            stats["референсов_снято"] = stats.get("референсов_снято", 0) + (1 if p.is_file() else 0)
            continue
        if p.is_dir():
            continue                      # папки создаются под файлы: пустых в витрине нет
        out = dst / hub_rel(rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix.lower() in TEXT_EXT and p.name not in RAW_FILES:
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copyfile(p, out)
                continue
            t, st = scrub(text, code=p.suffix.lower() in (".py", ".js"))
            for k, v in st.items():
                stats[k] = stats.get(k, 0) + v
            t, n = relayout(t)
            stats["путей_переписано"] = stats.get("путей_переписано", 0) + n
            if t != text:
                stats["файлов_изменено"] = stats.get("файлов_изменено", 0) + 1
            out.write_text(t, encoding="utf-8")
        else:
            shutil.copyfile(p, out)


def fill(template: Path, **kw) -> str:
    s = template.read_text(encoding="utf-8")
    for k, v in kw.items():
        s = s.replace("{{" + k + "}}", str(v))
    return s


def write_zip(src: Path, zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_dir() or p.suffix == ".pyc" or "__pycache__" in p.parts:
                continue
            zi = zipfile.ZipInfo(str(Path(src.name) / p.relative_to(src)).replace("\\", "/"), date_time=ZIP_TS)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.flag_bits |= 0x800          # имена в UTF-8 — явно
            z.writestr(zi, p.read_bytes())
    return hashlib.sha256(zip_path.read_bytes()).hexdigest()


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    # Проверки запускаются внутри витрины до упаковки: без этого флага Python
    # оставлял в ней __pycache__ (дефект архивов V3.3 и V3.4 — 5 файлов .pyc).
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, env=env)


def run_parallel(jobs: list) -> list:
    """Запустить несколько проверок одновременно; результаты — в порядке заданий."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    procs = [subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, env=env) for cmd, cwd in jobs]
    out = []
    for (cmd, _), pr in zip(jobs, procs):
        so, se = pr.communicate()
        out.append(subprocess.CompletedProcess(cmd, pr.returncode, so, se))
    return out


def version_int(version: str) -> int:
    """V3.5 → 35, V4 → 40: целое для поля version каталога, растёт монотонно."""
    m = re.match(r"V?(\d+)(?:\.(\d+))?", version.strip())
    if not m:
        return 1
    return int(m.group(1)) * 10 + int(m.group(2) or 0)


SERVICE_KEYS = ("id", "версия агента", "обновлено", "спецификация")


def hub_skill_header(text: str, vint: int) -> str:
    """Заголовок навыка витрины: только поля каталога, version — целое число."""
    if not text.startswith("---\n"):
        return text
    end = text.index("\n---\n", 4)
    lines = []
    for ln in text[4:end].splitlines():
        key = ln.split(":", 1)[0].strip().lower()
        if key in SERVICE_KEYS:
            continue
        if key == "version":
            ln = f"version: {vint}"
        lines.append(ln)
    return "---\n" + "\n".join(lines) + text[end:]


def _plain_problems(where: str, value: str) -> list[str]:
    out = []
    if not value:
        out.append(f"{where}: пусто")
    elif value[:1] in "|>'\"":
        out.append(f"{where}: нужна одна plain-строка (без блочного скаляра и кавычек)")
    elif ": " in value:
        out.append(f"{where}: «: » внутри строки ломает разбор")
    if re.search(r"\bтриггер", value, re.I):
        out.append(f"{where}: триггеры — в теле навыка (раздел «Когда использовать»), не в описании")
    return out


LINK_REFS = re.compile(REFS_HUB + r"/([^`\s)\]»\"',;]+)")


def hub_layout_problems(hub: Path) -> list[str]:
    """Раскладка навыка: «Референсы/» одним уровнем, `references/` не осталось,
    каждая ссылка на справочник ведёт к файлу."""
    probs: list[str] = []
    for sk in sorted(d for d in (hub / "skills").iterdir() if d.is_dir()):
        name = f"skills/{sk.name}"
        refs = sk / REFS_HUB
        for old in ("references", "templates"):
            if (sk / old).exists():
                probs.append(f"{name}: папка {old}/ — справочники и шаблоны лежат в {REFS_HUB}/")
        if not refs.is_dir():
            probs.append(f"{name}: нет папки {REFS_HUB}/")
            continue
        nested = sorted(p.name for p in refs.iterdir() if p.is_dir())
        if nested:
            probs.append(f"{name}/{REFS_HUB}: вложенные папки {', '.join(nested)} — ссылки только на один уровень")
        leftovers, broken = [], set()
        for f in sorted(sk.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in TEXT_EXT or f.name in RAW_FILES:
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if re.search(r"\breferences\b|references/", text):
                leftovers.append(str(f.relative_to(sk)))
            if f.suffix.lower() != ".md":
                continue
            for m in LINK_REFS.finditer(text):
                target = m.group(1).rstrip(".")
                if "*" in target or "<" in target or not target.endswith(".md"):
                    continue
                if "/" in target:
                    broken.add(f"{REFS_HUB}/{target} (вложенный путь)")
                elif not (refs / target).is_file():
                    broken.add(f"{REFS_HUB}/{target}")
        if leftovers:
            probs.append(f"{name}: «references» осталось в {len(leftovers)} файлах: {', '.join(leftovers[:4])}")
        if broken:
            probs.append(f"{name}: ссылки на справочники не ведут к файлу — {', '.join(sorted(broken)[:5])}")
    return probs


def hub_contract_problems(hub: Path) -> list[str]:
    """Проверка заголовков витрины по стандарту каталога — до упаковки."""
    probs: list[str] = []
    py = skill_header.parse((hub / "plugin.yaml").read_text(encoding="utf-8"))
    if not py.get("name"):
        probs.append("plugin.yaml: нет name")
    probs += _plain_problems("plugin.yaml: description", py.get("description", ""))
    if py.get("category") not in skill_header.CATALOG_CATEGORIES:
        probs.append(f"plugin.yaml: category «{py.get('category')}» не из семи категорий каталога")
    if not py.get("version", "").isdigit():
        probs.append(f"plugin.yaml: version «{py.get('version')}» — нужно целое число")
    tags = [t.strip() for t in py.get("tags", "").strip("[]").split(",") if t.strip()]
    if not tags or any(not skill_header.TAG.match(t) for t in tags):
        probs.append("plugin.yaml: tags — непустой список slug (строчная латиница, цифры, дефис)")
    skills = sorted(d.name for d in (hub / "skills").iterdir() if d.is_dir())
    for sk in skills:
        text = (hub / "skills" / sk / "SKILL.md").read_text(encoding="utf-8")
        fm = text.split("---", 2)[1] if text.startswith("---") else ""
        probs += [f"skills/{sk}/SKILL.md: {x}" for x in skill_header.problems(fm, target="catalog")]
        probs += [f"skills/{sk}/SKILL.md: {x}" for x in skill_header.body_triggers_problems(text)]
        extra = [k for k in skill_header.parse(fm) if k in SERVICE_KEYS]
        if extra:
            probs.append(f"skills/{sk}/SKILL.md: служебные поля в заголовке витрины — {', '.join(extra)}")
        n_body = len(text.split("---", 2)[2].splitlines()) if text.startswith("---") else len(text.splitlines())
        if n_body > 500:
            probs.append(f"skills/{sk}/SKILL.md: тело {n_body} строк — больше 500, лишнее в справочники")
    for a in sorted((hub / "agents").glob("*.md")):
        d = skill_header.parse(a.read_text(encoding="utf-8").split("---", 2)[1])
        if not d.get("name"):
            probs.append(f"agents/{a.name}: нет name")
        probs += _plain_problems(f"agents/{a.name}: description", d.get("description", ""))
        used = [t.strip() for t in d.get("skills", "").strip("[]").split(",") if t.strip()]
        alien = [u for u in used if u not in skills]
        if not used or alien:
            probs.append(f"agents/{a.name}: skills должен ссылаться только на навыки плагина ({', '.join(skills)})")
    n_args = 0
    for c in sorted((hub / "commands").glob("*.md")):
        text = c.read_text(encoding="utf-8")
        d = skill_header.parse(text.split("---", 2)[1])
        if not d.get("name"):
            probs.append(f"commands/{c.name}: нет name")
        probs += [x for x in _plain_problems(f"commands/{c.name}: description", d.get("description", ""))
                  if "триггер" not in x]
        body = [ln for ln in text.split("---", 2)[2].splitlines() if ln.strip()]
        if not body or len(body) > CMD_BODY_MAX:
            probs.append(f"commands/{c.name}: тело {len(body)} строк — нужен короткий запуск (1–{CMD_BODY_MAX})")
        args = ARG.findall(text)
        n_args += len(args)
        bad = [x for x in args if not re.fullmatch(r"[a-zа-яё_]+", x)]
        if bad:
            probs.append(f"commands/{c.name}: параметры ${{…}} — строчные буквы и «_»: {', '.join(bad)}")
    if not n_args:
        probs.append("commands: ни одной команды с параметром ${…}")
    probs += hub_layout_problems(hub)
    for f in ("plugin.yaml", "README.md", "agent.html", "agents/agent-factory.md", *[f"commands/{c}" for c in HUB_COMMANDS]):
        if (hub / f).exists() and re.search(r"\{\{\w+\}\}", (hub / f).read_text(encoding="utf-8")):
            probs.append(f"{f}: незаполненный шаблон {{{{…}}}}")
    for f in ("README.md", "agent.html"):
        t = (hub / f).read_text(encoding="utf-8") if (hub / f).exists() else ""
        # Нейтральное оформление: системные шрифты, без подключаемых шрифтов и
        # без токенов внутренней дизайн-системы (префикс --gc-).
        quoted = [q for decl in re.findall(r"font-family\s*:([^;}]*)", t)
                  for q in re.findall(r'"([^"]+)"', decl) if q != "Segoe UI"]
        if re.search(r"@font-face|--gc-", t) or quoted:
            probs.append(f"{f}: подключаемые шрифты или внутренние токены оформления — витрина в нейтральном стиле")
    junk = [str(p.relative_to(hub)) for p in hub.rglob("*") if p.suffix == ".pyc" or p.name == "__pycache__"]
    if junk:
        probs.append(f"в витрине есть .pyc/__pycache__: {len(junk)}")
    return probs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Витринная сборка плагина")
    ap.add_argument("--out", required=True, help="папка результата (создаётся; hub/ внутри пересобирается)")
    ap.add_argument("--version", default=None, help="версия витрины, по умолчанию — из SKILL.md")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--no-check", action="store_true", help="не запускать шлюз и проверки внутри сборки")
    ap.add_argument("--стоп-слова", dest="stop", default=None, help="приватный стоп-лист для hub_gate (вне Git)")
    ap.add_argument("--с-примерами", dest="with_examples", action="store_true",
                    help="перенести examples/ и шаблоны базы (по умолчанию витрина без референсов)")
    args = ap.parse_args(argv)

    skill_md = (PLUGIN / "SKILL.md").read_text(encoding="utf-8")
    m = re.search(r"^версия агента:\s*(\S+)", skill_md, re.M)
    version = args.version or (m.group(1) if m else "V0")
    version_num = version_int(version)
    out = Path(args.out).resolve()
    hub = out / "hub" / "agent-factory"
    if hub.parent.exists():
        shutil.rmtree(hub.parent)
    skill = hub / "skills" / "agent-factory"
    skill.mkdir(parents=True)

    stats: dict = {}
    copy_skill(skill, stats, args.with_examples)
    sk_md = skill / "SKILL.md"
    sk_md.write_text(hub_skill_header(sk_md.read_text(encoding="utf-8"), version_num), encoding="utf-8")
    if not args.with_examples:
        # база шаблонов пуста — индекс пересобирается тем же скриптом, что и в репозитории
        run([sys.executable, "scripts/templates.py", "--индекс"], skill)
        # описание базы в репозитории перечисляет шаблоны — в витрине их нет, и описание
        # обязано говорить то же, что лежит рядом, а не то, что лежит в репозитории
        (skill / "agent_templates" / "00_ЧТО_ЗДЕСЬ_И_ЧЕГО_ЗДЕСЬ_НЕТ.md").write_text(
            relayout(HUB_TEMPLATES_README)[0], encoding="utf-8")
        skill_text = (skill / "SKILL.md").read_text(encoding="utf-8")
        skill_text = re.sub(r"^\| `examples/1c-dev-assistant/` \|[^\n]*\n", "", skill_text, flags=re.M)
        (skill / "SKILL.md").write_text(skill_text, encoding="utf-8")
    # копилки — пустые по политике: реестр и журнал остаются файлами, но без записей
    reg = skill / "knowledge" / "registry.yaml"
    if reg.exists():
        head = [ln for ln in reg.read_text(encoding="utf-8").splitlines() if ln.startswith("#")]
        reg.write_text("\n".join(head + ["[]"]) + "\n", encoding="utf-8")

    # команды витрины
    cmds_dir = hub / "commands"
    cmds_dir.mkdir()
    n_cmd = 0
    for name in HUB_COMMANDS:
        (cmds_dir / name).write_text((PLUGIN / "templates" / "hub" / "commands" / name).read_text(encoding="utf-8"),
                                     encoding="utf-8")
        n_cmd += 1
    # обёртка
    tpl = PLUGIN / "templates" / "hub"
    refs = [p for p in (PLUGIN / "references").glob("*.md")] + []
    scripts = [p for p in (PLUGIN / "scripts").glob("*.py")]
    # Уровни агента — из того же источника, что паспорт и путь пользователя:
    # «пилот» в описании витрины значит то же, что в паспорте агента.
    from build_user_package import УРОВНИ
    import html as _html
    levels_table = "\n".join(["   | Уровень | Что это | Что нужно |", "   |---|---|---|"]
                             + [f"   | {k} | {v[0]} | {v[1]} |" for k, v in УРОВНИ.items()])
    levels_rows = "\n      ".join(f"<tr><td>{_html.escape(k)}</td><td>{_html.escape(v[0])}</td>"
                                  f"<td>{_html.escape(v[1])}</td></tr>" for k, v in УРОВНИ.items())
    kw = dict(refs=len(refs), scripts=len(scripts), version=version, version_num=version_num, commands=n_cmd,
              date=args.date, levels_table=levels_table, levels_rows=levels_rows)
    (hub / "plugin.yaml").write_text(fill(tpl / "plugin.yaml", **kw), encoding="utf-8")
    (hub / "agents").mkdir()
    (hub / "agents" / "agent-factory.md").write_text(fill(tpl / "agent.md", **kw), encoding="utf-8")
    (hub / "agent.html").write_text(fill(tpl / "agent.html", **kw), encoding="utf-8")
    (hub / "README.md").write_text(fill(tpl / "README.md", **kw), encoding="utf-8")

    # --- шлюз и проверки внутри сборки ---
    gate_line, self_line, deliv_line, gate_out = "не запускался", "не запускались", "не запускались", ""
    ok = True
    if not args.no_check:
        gate_cmd = [sys.executable, str(HERE / "hub_gate.py"), "--папка", str(hub), "--строго"]
        if args.stop:
            gate_cmd += ["--стоп-слова", args.stop]
        # Три проверки независимы — запускаются параллельно, сборка вдвое быстрее.
        # selftest.py нужен пример (examples/), которого в витрине нет намеренно:
        # он запускается на источнике сборки — той же кодовой базе.
        r, r1, r2 = run_parallel([
            (gate_cmd, hub),
            ([sys.executable, "scripts/selftest.py"], skill if args.with_examples else PLUGIN),
            ([sys.executable, "scripts/selftest_delivery.py"], skill),
        ])
        gate_out = r.stdout
        gate_line = "ЗЕЛЁНЫЙ (0 нарушений)" if r.returncode == 0 else f"КРАСНЫЙ (код {r.returncode})"
        ok &= r.returncode == 0
        tail = (r1.stdout.strip().splitlines() or [""])[-1]
        self_line = f"{tail} (код {r1.returncode}; {'внутри витрины' if args.with_examples else 'на источнике сборки — в витрине нет примера'})"
        ok &= r1.returncode == 0
        tail2 = (r2.stdout.strip().splitlines() or [""])[-1]
        deliv_line = f"{tail2} (код {r2.returncode})"
        ok &= r2.returncode == 0

    # --- заголовки по стандарту каталога и чистота архива ---
    for junk in [p for p in hub.rglob("__pycache__") if p.is_dir()]:
        shutil.rmtree(junk)
    contract = hub_contract_problems(hub)
    contract_line = "ЗЕЛЁНЫЙ" if not contract else f"КРАСНЫЙ ({len(contract)})"
    ok &= not contract

    # --- архив ---
    zip_path = out / f"agent-factory_hub_{version}.zip"
    sha = write_zip(hub, zip_path)
    n_files = sum(1 for p in hub.rglob("*") if p.is_file())
    n_ref = stats.get("референсов_снято", 0)
    cyr = sum(1 for p in hub.rglob("*") if p.is_file() and re.search(r"[А-Яа-яЁё]", str(p.relative_to(hub))))
    report = [f"# Сборка витрины — Фабрика агентов GigaCowork {version}", "",
              f"- Дата: {args.date}", f"- Источник: `plugin/agent-factory` (SKILL.md {version})",
              f"- Раскладка: plugin.yaml · agents/agent-factory.md · commands/ ({n_cmd}) · skills/agent-factory/ · agent.html · README.md",
              f"- Навык: SKILL.md · {REFS_HUB}/ одним уровнем · scripts/ · данные скриптов agent_templates/ и knowledge/; путей переписано {stats.get('путей_переписано', 0)}",
              f"- Файлов: {n_files}, из них с кириллицей в пути: {cyr} (архив с именами в UTF-8, собран Python zipfile)",
              f"- Справочников: {len(refs)} · скриптов: {len(scripts)}",
              f"- Вырезано провенанса: дат {stats.get('даты', 0)}, коротких дат у псевдонимов {stats.get('даты_у_псевдонима', 0)}, "
              f"адресов платформы {stats.get('платформа', 0)}; файлов изменено {stats.get('файлов_изменено', 0)}",
              f"- Референсы: {'перенесены (--с-примерами)' if args.with_examples else f'сняты — {n_ref} файлов примера и шаблонов; агент собирается с нуля по спецификации'}",
              f"- Копилки: реестр агентов пуст, журнал находок пуст, база шаблонов {'с примером' if args.with_examples else 'пуста'}",
              f"- Шлюз обезличивания (строгий): {gate_line}",
              f"- selftest внутри сборки: {self_line}",
              f"- selftest_delivery внутри сборки: {deliv_line}",
              f"- Заголовки по стандарту каталога (plugin.yaml, навык, агент, команды), раскладка «{REFS_HUB}/» и архив без .pyc: {contract_line}",
              f"- Архив: `{zip_path.name}` · SHA-256 {sha}", ""]
    if contract:
        report += ["## Нарушения стандарта каталога", ""] + [f"- {x}" for x in contract] + [""]
    if gate_out and "КРАСНЫЙ" in gate_line:
        report += ["## Нарушения шлюза", "", "```", gate_out.strip(), "```", ""]
    (out / "СБОРКА_ВИТРИНЫ.md").write_text("\n".join(report), encoding="utf-8")
    (out / "СБОРКА_ВИТРИНЫ.json").write_text(json.dumps({"version": version, "sha256": sha, "files": n_files,
                                                         "gate": gate_line, "selftest": self_line, "selftest_delivery": deliv_line,
                                                         "catalog_contract": contract,
                                                         "scrubbed": stats}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(report))
    return EXIT_OK if ok else EXIT_RED


if __name__ == "__main__":
    sys.exit(main())
