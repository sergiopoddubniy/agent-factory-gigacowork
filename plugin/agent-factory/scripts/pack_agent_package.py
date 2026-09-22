#!/usr/bin/env python3
"""
pack_agent_package.py — упаковка и структурный аудит готового пакета агента GigaCowork
(перенесён из исходного скилла как build_agent_package.py; в фабрике пакет
порождается из спецификации сборщиком build_agent_package.py --spec, а этот
скрипт упаковывает и проверяет уже написанные SKILL.md и run-agent.md —
например, унаследованный агент без спецификации или пакет из портфеля).

Собирает единственно допустимую структуру:

    <agent-slug>/
    ├── commands/run-agent.md
    └── skills/00-agent-skill/SKILL.md

проводит structural audit и раскладывает открытые файлы в папку агента.
При audit_status: FAIL архив не создаётся.

Использование:

    python3 pack_agent_package.py \
        --slug contract-completeness-check \
        --skill <папка работы>/SKILL.md \
        --command <папка работы>/run-agent.md \
        --out <папка выдачи>

Временная папка сборки берётся из системного temp; переопределяется
флагом --work, если нужно.

Только stdlib, никаких зависимостей.
"""

import argparse
import hashlib
import re
import unicodedata
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

DEFAULT_WORK = Path(tempfile.gettempdir()) / "_agent_build"

# Фиксированная метка времени в архиве: иначе SHA-256 меняется на каждой
# пересборке того же содержимого, и его нельзя предъявить как «это тот
# самый пакет, который получил клиент».
ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)

EXIT_OK, EXIT_AUDIT_FAIL, EXIT_INPUT_ERROR = 0, 1, 2


def write_zip(zip_path: Path, root: Path) -> None:
    """Детерминированный архив: порядок файлов и метки времени фиксированы,
    поэтому одинаковое содержимое всегда даёт одинаковый SHA-256."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(
                path.relative_to(root.parent).as_posix(), date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())

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

REQUIRED_GATES = [
    "scope_check", "input_check", "source_check", "consistency_check",
    "output_check", "safety_check", "demo_check", "handoff_check",
]

REQUIRED_CASES = [
    "happy_path", "missing_required_input", "conflicting_sources",
    "prompt_injection_in_document", "external_action_without_confirmation",
]

FORBIDDEN_NAMES = {
    "readme.md", "agents.md", "manifest.md", "checksums.sha256",
    "package_audit.md", "system_prompt_for_agent_builder.md",
}
FORBIDDEN_DIR_PREFIXES = (
    "workflow", "policy", "policies", "template", "templates", "eval",
    "evals", "schema", "schemas", "demo", "script", "scripts",
)

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _nfc(s: str) -> str:
    """Приводит текст к составленной форме юникода.

    macOS хранит «ё» и «й» разложенными, и файл, созданный в Finder, не
    совпадал с образцами разделов вида «Работа с неопределённостью» — аудит
    отказывал на верном файле. Найдено метаморфным тестом на боевом портфеле;
    тот же класс ошибки к этому моменту повторился в проекте четырежды.
    """
    return unicodedata.normalize("NFC", s)


def read(path: Path, label: str = "файл") -> str:
    """Отсутствующий или нечитаемый файл — это внятный отказ, а не traceback:
    опечатка в пути должна показывать, что не найдено, а не стек Python."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        print(f"audit_status: FAIL\ncritical_findings:\n"
              f"  - не найден входной файл «{label}»: {path}\n"
              f"    проверь путь в аргументе --{label}; архив не собран",
              file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)
    except UnicodeDecodeError:
        print(f"audit_status: FAIL\ncritical_findings:\n"
              f"  - файл «{label}» не читается как UTF-8: {path}\n"
              f"    пересохрани в UTF-8 без BOM; архив не собран",
              file=sys.stderr)
        sys.exit(EXIT_INPUT_ERROR)


def frontmatter(text: str) -> str | None:
    if not text.lstrip().startswith("---"):
        return None
    head = text.lstrip().split("---", 2)
    return head[1] if len(head) >= 3 else None


def skill_frontmatter_problems(text: str) -> list[str]:
    """Фронтматтер `SKILL.md` повторяет форму «Создать навык» в GigaCowork.

    Поля формы: «Имя навыка» (обязательное), «Когда применять»
    (обязательное), «Категория» (необязательное), «Инструкции»
    (обязательное — это тело файла). Фронтматтер называет их теми же
    словами, чтобы сборщик переносил значения в форму один в один, ничего
    не переводя по дороге.

    До 25.08 здесь требовалось `name: 00-agent-skill` и `summary:` — это была
    наша выдумка, а не поле платформы, и она пережила несколько ревизий
    только потому, что проверялась нашим же скриптом. Урок общий:
    инструмент, написанный по тому же предположению, подтверждает
    предположение, а не реальность.
    """
    fm = frontmatter(text)
    if fm is None:
        return ["SKILL.md: нет YAML-фронтматтера"]

    # Ключи содержат «й» и «ё»: в NFD они разложены на букву и диакритику, и
    # регулярное выражение по составленной форме их не находит. Файл,
    # сохранённый на macOS, приходит именно в NFD. Приводим к NFC до разбора.
    fm = _nfc(fm)
    problems = []
    name = re.search(r'^\s*имя навыка:\s*["\']?(.+?)["\']?\s*$', fm, re.M | re.I)
    if not name:
        problems.append("SKILL.md: нет поля «имя навыка» — это обязательное "
                        "поле формы «Создать навык»")
    elif not re.search(r"[А-Яа-яЁё]", name.group(1)):
        problems.append(f"SKILL.md: «имя навыка: {name.group(1)}» — это имя "
                        "видит пользователь в каталоге навыков, оно должно быть "
                        "на языке пользователя, а не служебным слагом")

    when = re.search(r'^\s*когда применять:\s*(.*)$', fm, re.M | re.I)
    if not when:
        problems.append("SKILL.md: нет поля «когда применять» — обязательное "
                        "поле формы; по нему агент решает, брать навык или нет")
    else:
        block = fm[when.end():]
        body = when.group(1).strip().lstrip("|>-").strip() or block.strip()
        if len(body) < 80:
            problems.append("SKILL.md: «когда применять» слишком коротко — "
                            "это условие отбора навыка («в каких случаях агент "
                            "берёт навык»), а не пересказ названия")

    if re.search(r"^\s*summary:", fm, re.M):
        problems.append("SKILL.md: поле «summary» в форме платформы отсутствует; "
                        "оно не переносится никуда и расходится с «когда применять»")
    return problems


def has_frontmatter(text: str, expected_name: str) -> bool:
    """Фронтматтер файла команды: имя — константа упаковки."""
    fm = frontmatter(text)
    if fm is None:
        return False
    name_ok = re.search(
        r'^\s*name:\s*["\']?' + re.escape(expected_name) + r'["\']?\s*$',
        fm, re.M) is not None
    return name_ok and "version:" in fm and "summary:" in fm


# Разделы вспомогательного навыка. Их мало и они другие: справочный навык
# не задаёт агента, он описывает работу с инструментом. Требовать от него
# семнадцать разделов агентского навыка — значит либо не выпустить его, либо
# набить пустыми заголовками; и то и другое хуже, чем отдельный вид.
REFERENCE_SECTIONS = [
    "Чего в этом навыке нет",
]


def audit_reference(skill_text: str) -> list[str]:
    """Аудит вспомогательного навыка пространства.

    Отличается от навыка агента по существу, а не по объёму:

    | | Навык агента | Вспомогательный навык |
    |---|---|---|
    | Что задаёт | агента целиком: роль, границы, шлюзы, приёмку | порядок работы с инструментом |
    | Кто активирует | карточка агента, один навык на агента | любой агент, когда задача требует |
    | Что сломается без него | агента нет | агент пойдёт в инструмент наугад |

    Поэтому обязательных разделов здесь один, и он тот же, что делает
    полезной карту состава базы знаний: **честный перечень того, чего в
    навыке нет**. Без него молчание читается как разрешение догадываться —
    ровно так агент и придумал несуществующий метод поиска тендеров.
    """
    problems = skill_frontmatter_problems(skill_text)
    low = _nfc(skill_text).lower()
    headings = [re.sub(r"^#+\s*(\d+[.)]\s*)?", "", ln).strip().lower()
                for ln in low.splitlines() if ln.lstrip().startswith("#")]
    for section in REFERENCE_SECTIONS:
        if not any(_nfc(section).lower() in h for h in headings):
            problems.append(f"навык-справочник: нет раздела «{section}» — без него "
                            f"молчание читается как разрешение догадываться")
    if len(skill_text) < 1500:
        problems.append("навык-справочник короче 1500 знаков: скорее всего, это "
                        "описание инструмента, а его место в самом коннекторе")
    return problems


def audit(skill_text: str, command_text: str, slug: str) -> dict:
    findings, missing_sections, notes = [], [], []

    if not SLUG_RE.match(slug):
        findings.append(f"slug '{slug}' не в kebab-case (латиница, дефисы)")

    findings.extend(skill_frontmatter_problems(skill_text))
    if not has_frontmatter(command_text, "run-agent"):
        findings.append("run-agent.md: отсутствует или неполный YAML-фронтматтер "
                        "(нужны name: run-agent, summary, version)")

    # Н-4. Раздел ищется как ЗАГОЛОВОК, а не как строка где угодно в тексте.
    # Пока проверялось вхождение подстроки, вырезанный целиком раздел
    # «HITL_REQUIRED» проходил аудит: слово оставалось в списке quality gates
    # и в правилах. Аудит подтверждал наличие того, чего в навыке уже не было.
    low = _nfc(skill_text).lower()
    headings = [re.sub(r"^#+\s*(\d+[.)]\s*)?", "", ln).strip().lower()
                for ln in low.splitlines() if ln.lstrip().startswith("#")]
    for section in REQUIRED_SECTIONS:
        s = _nfc(section).lower()
        if not any(s in h for h in headings):
            missing_sections.append(section)
    if missing_sections:
        findings.append("SKILL.md: не найдено разделов — "
                        + ", ".join(missing_sections))

    missing_gates = [g for g in REQUIRED_GATES if g not in skill_text]
    if missing_gates:
        findings.append("SKILL.md: отсутствуют quality gates — " + ", ".join(missing_gates))

    missing_cases = [c for c in REQUIRED_CASES if c not in skill_text]
    if missing_cases:
        findings.append("SKILL.md: отсутствуют acceptance cases — " + ", ".join(missing_cases))

    if "SIM_TOOL_CALL" not in skill_text or "SIM_TOOL_RESULT" not in skill_text:
        findings.append("SKILL.md: не описана конвенция SIM_TOOL_CALL / SIM_TOOL_RESULT")
    if "external_write" not in skill_text:
        findings.append("SKILL.md: не задан external_write=false для demo-контура")

    # Команда обязана АКТИВИРОВАТЬ навык. Требовать при этом путь внутри
    # архива нельзя: агент на платформе такого пути не видит, он видит имя
    # навыка — заголовок первого уровня `SKILL.md`. Аудит, требовавший путь,
    # противоречил правилу самого скилла и заставлял оставлять в теле
    # команды ссылку в никуда.
    skill_h1 = re.search(r"^#\s+(.+)$", _nfc(skill_text), re.M)
    name = _nfc(skill_h1.group(1)).strip().strip("«»\"'") if skill_h1 else None
    cmd_nfc = _nfc(command_text)
    activates = "активируй навык" in cmd_nfc.lower()
    names_it = bool(name) and name.lower() in cmd_nfc.lower()
    if not activates:
        findings.append("run-agent.md: команда не активирует навык — нет блока "
                        "«Активируй навык …»")
    elif not names_it and "skills/00-agent-skill/SKILL.md" not in cmd_nfc:
        findings.append(f"run-agent.md: команда активирует навык, но не называет "
                        f"его по имени «{name}» — агент видит навык по имени, "
                        f"а не по пути в архиве")
    if "Контур" not in command_text:
        findings.append("run-agent.md: не задан параметр «Контур»")

    for word in ("интеграция подключена", "write-back выполнен", "данные записаны в"):
        if word in skill_text.lower():
            notes.append(f"проверь формулировку: «{word}» может читаться как обещание реальной интеграции")

    return {
        "status": "FAIL" if findings else "PASS",
        "findings": findings,
        "missing_sections": missing_sections,
        "notes": notes,
    }


def check_tree(root: Path) -> list:
    """Проверяет, что в собранном дереве нет ничего лишнего."""
    problems = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        parts = rel.parts
        if path.is_dir():
            if parts[0] not in ("skills", "commands"):
                problems.append(f"лишний каталог: {rel}")
            elif len(parts) > 1 and parts[0] == "commands":
                problems.append(f"лишний вложенный каталог: {rel}")
            continue
        if path.name.lower() in FORBIDDEN_NAMES:
            problems.append(f"запрещённый файл: {rel}")
        if parts[0].lower().startswith(FORBIDDEN_DIR_PREFIXES) and parts[0] not in ("skills", "commands"):
            problems.append(f"запрещённый каталог: {parts[0]}")
    skills = list(root.glob("skills/*/SKILL.md"))
    commands = list(root.glob("commands/*.md"))
    if len(skills) != 1:
        problems.append(f"навыков должно быть ровно 1, найдено {len(skills)}")
    if len(commands) != 1:
        problems.append(f"команд должно быть ровно 1, найдено {len(commands)}")
    return problems


def yaml_list(items) -> str:
    if not items:
        return "[]"
    return "\n" + "\n".join(f"  - {i}" for i in items)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--skill", required=True, type=Path)
    ap.add_argument("--command", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--номер", dest="номер", default="",
                    help="номер агента в портфеле: папка станет <N>_<slug>")
    ap.add_argument("--вид", dest="kind", default="агент",
                    choices=["агент", "справочный"],
                    help="агент — навык агента (17 разделов, gates, acceptance "
                         "cases); справочный — вспомогательный навык пространства")
    ap.add_argument("--work", type=Path, default=DEFAULT_WORK,
                    help=f"временная папка сборки (по умолчанию {DEFAULT_WORK})")
    args = ap.parse_args()

    skill_text = read(args.skill, "skill")
    command_text = read(args.command, "command")

    if args.kind == "справочный":
        problems = audit_reference(skill_text)
        result = {"status": "PASS" if not problems else "FAIL",
                  "missing_sections": [], "findings": problems,
                  "notes": ["вид навыка: справочный — проверяется фронтматтер, "
                            "перечень «чего нет» и объём; разделы навыка агента "
                            "не требуются"]}
    else:
        result = audit(skill_text, command_text, args.slug)

    root = args.work / args.slug
    if root.exists():
        shutil.rmtree(root)
    (root / "skills" / "00-agent-skill").mkdir(parents=True)
    (root / "commands").mkdir(parents=True)
    (root / "skills" / "00-agent-skill" / "SKILL.md").write_text(skill_text, encoding="utf-8")
    (root / "commands" / "run-agent.md").write_text(command_text, encoding="utf-8")

    tree_problems = check_tree(root)
    if tree_problems:
        result["findings"].extend(tree_problems)
        result["status"] = "FAIL"

    skills_count = len(list(root.glob("skills/*/SKILL.md")))
    commands_count = len(list(root.glob("commands/*.md")))
    forbidden = sum(1 for f in result["findings"] if "запрещённ" in f)

    print("audit_status: " + result["status"])
    print(f"skills_count: {skills_count}")
    print(f"commands_count: {commands_count}")
    print(f"forbidden_files_count: {forbidden}")
    print("missing_sections:" + yaml_list(result["missing_sections"]))
    print("critical_findings:" + yaml_list(result["findings"]))
    print("notes:" + yaml_list(result["notes"]))

    if result["status"] == "FAIL":
        print("\nПакет не собран: устрани critical_findings и запусти повторно.", file=sys.stderr)
        return EXIT_AUDIT_FAIL

    # Выдача — ОТКРЫТЫЕ файлы в папке агента. Архив не собирается: из него
    # не видно ни содержимого, ни расхождений, сверка комплекта его не читает,
    # а перенос агента в конструктор требует сначала распаковать. Конструктор
    # Поколения 2 принимает загрузку архивом — упаковку делает человек перед
    # самой загрузкой, по инструкции по сборке.
    папка = args.out / (f"{args.номер}_{args.slug}" if args.номер else args.slug)
    папка.mkdir(parents=True, exist_ok=True)
    навык = папка / f"00_Навык_{args.slug}.md"
    команды = папка / f"02_Команды_{args.slug}.md"
    навык.write_text(args.skill.read_text(encoding="utf-8"), encoding="utf-8")
    команды.write_text(args.command.read_text(encoding="utf-8"),
                       encoding="utf-8")

    digest = hashlib.sha256(навык.read_bytes()).hexdigest()
    print(f"\nagent_dir: {папка}")
    print(f"skill_file: {навык.name}")
    print(f"commands_file: {команды.name}")
    print(f"sha256_навыка: {digest}")
    print("archive: не собирается — в поставке архивов нет; упаковку под "
          "загрузку в конструктор делает человек по инструкции по сборке")
    print("platform_runtime_validation: требуется выполнить в конструкторе — "
          "это действие ЧЕЛОВЕКА в интерфейсе платформы, скрипт его не заменяет")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
