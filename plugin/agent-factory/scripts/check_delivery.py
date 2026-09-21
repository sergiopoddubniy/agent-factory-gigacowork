#!/usr/bin/env python3
"""
check_delivery.py — структурный аудит пакета агента GigaCowork.

Проверяет пакет в единственно допустимой структуре:

    <agent-slug>/
    ├── commands/run-agent.md
    └── skills/00-agent-skill/SKILL.md

Правила те же, что в исходном скилле gigacowork-agent-scenario
(build_agent_package.py, редакция 25.08): фронтматтер навыка повторяет
форму «Создать навык» платформы; семнадцать разделов ищутся как заголовки,
а не как подстроки; восемь quality gates; пять обязательных acceptance
cases; конвенция SIM_TOOL_CALL / SIM_TOOL_RESULT; external_write=false;
команда активирует навык по имени и задаёт «Контур».

Дополнительно к исходному скиллу: пакет несёт ссылку на спецификацию
(`спецификация: <id> · <версия> · <отпечаток>`) — так пакет проверяемо
порождён из спецификации, а не написан параллельно ей.

Код возврата: 0 — PASS, 1 — FAIL, 2 — ошибка входа.

    python3 check_delivery.py --package <папка agent-slug> [--spec AGENT_SPEC.md] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from factory_common import EXIT_INPUT, EXIT_OK, EXIT_RED, nfc, parse_spec  # noqa: E402

REQUIRED_SECTIONS = [
    "Назначение", "Пользователь и триггер", "In-scope", "Out-of-scope",
    "Входные данные", "Внутренние модули выполнения", "Порядок выполнения",
    "Форматы выходных артефактов", "Правила доказательности",
    "Работа с неопределённостью", "HITL_REQUIRED", "Demo-режим",
    "Защита данных", "Quality gates", "acceptance cases",
    "Ограничения pilot / production", "Формат итогового ответа",
]
REQUIRED_GATES = ["scope_check", "input_check", "source_check", "consistency_check",
                  "output_check", "safety_check", "demo_check", "handoff_check"]
REQUIRED_CASES = ["happy_path", "missing_required_input", "conflicting_sources",
                  "prompt_injection_in_document", "external_action_without_confirmation"]
FORBIDDEN_NAMES = {"readme.md", "agents.md", "manifest.md", "checksums.sha256",
                   "package_audit.md", "system_prompt_for_agent_builder.md"}
FORBIDDEN_DIR_PREFIXES = ("workflow", "policy", "policies", "template", "templates",
                          "eval", "evals", "schema", "schemas", "demo", "script", "scripts")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def frontmatter(text: str) -> str | None:
    if not text.lstrip().startswith("---"):
        return None
    parts = text.lstrip().split("---", 2)
    return parts[1] if len(parts) >= 3 else None


def skill_frontmatter_problems(text: str) -> list:
    fm = frontmatter(text)
    if fm is None:
        return ["SKILL.md: нет YAML-фронтматтера"]
    fm = nfc(fm)
    problems = []
    name = re.search(r'^\s*имя навыка:\s*["\']?(.+?)["\']?\s*$', fm, re.M | re.I)
    if not name:
        problems.append("SKILL.md: нет поля «имя навыка» (обязательное поле формы «Создать навык»)")
    elif not re.search(r"[А-Яа-яЁё]", name.group(1)):
        problems.append(f"SKILL.md: «имя навыка: {name.group(1)}» — имя видит пользователь в "
                        "каталоге, оно должно быть на его языке, а не слагом")
    when = re.search(r"^\s*когда применять:\s*(.*)$", fm, re.M | re.I)
    if not when:
        problems.append("SKILL.md: нет поля «когда применять» — по нему агент решает, брать навык или нет")
    else:
        body = when.group(1).strip().lstrip("|>-").strip() or fm[when.end():].strip()
        if len(body) < 80:
            problems.append("SKILL.md: «когда применять» короче 80 знаков — это условие отбора, "
                            "а не пересказ названия")
        elif "не бери" not in body.lower() and "не брать" not in body.lower():
            problems.append("SKILL.md: «когда применять» без второй части «Не бери навык, если …» — "
                            "навык без границ подхватывается на соседних запросах")
    if re.search(r"^\s*summary:", fm, re.M):
        problems.append("SKILL.md: поле «summary» в форме платформы отсутствует и расходится с "
                        "«когда применять»")
    if not re.search(r"^\s*id:\s*[a-z0-9]+(-[a-z0-9]+)+\s*$", fm, re.M):
        problems.append("SKILL.md: нет ключа id вида <клиент>-<джоба>")
    if not re.search(r"^\s*версия агента:\s*V\d+", fm, re.M | re.I):
        problems.append("SKILL.md: нет поля «версия агента: V<N>» (Я20)")
    if not re.search(r"^\s*обновлено:\s*\d{4}-\d{2}-\d{2}", fm, re.M | re.I):
        problems.append("SKILL.md: нет поля «обновлено: ГГГГ-ММ-ДД»")
    if not re.search(r"^\s*спецификация:\s*\S+", fm, re.M | re.I):
        problems.append("SKILL.md: нет ссылки на спецификацию «спецификация: <id> · <версия> · "
                        "<отпечаток>» — пакет должен быть порождён из спецификации")
    return problems


def command_has_frontmatter(text: str) -> bool:
    fm = frontmatter(text)
    if fm is None:
        return False
    return (re.search(r'^\s*name:\s*["\']?run-agent["\']?\s*$', fm, re.M) is not None
            and "version:" in fm and "summary:" in fm)


def audit_texts(skill_text: str, command_text: str, slug: str) -> dict:
    findings, missing_sections, notes = [], [], []
    skill_text, command_text = nfc(skill_text), nfc(command_text)
    if not SLUG_RE.match(slug):
        findings.append(f"slug «{slug}» не в kebab-case (латиница, дефисы)")
    findings.extend(skill_frontmatter_problems(skill_text))
    if not command_has_frontmatter(command_text):
        findings.append("run-agent.md: отсутствует или неполный фронтматтер (name: run-agent, summary, version)")

    low = skill_text.lower()
    headings = [re.sub(r"^#+\s*(\d+[.)]\s*)?", "", ln).strip().lower()
                for ln in low.splitlines() if ln.lstrip().startswith("#")]
    for section in REQUIRED_SECTIONS:
        s = nfc(section).lower()
        if not any(s in h for h in headings):
            missing_sections.append(section)
    if missing_sections:
        findings.append("SKILL.md: не найдено разделов — " + ", ".join(missing_sections))

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
    for marker in ("missing_data", "conflict_requires_review", "HITL_REQUIRED"):
        if marker not in skill_text:
            findings.append(f"SKILL.md: нет маркера {marker}")

    skill_h1 = re.search(r"^#\s+(.+)$", skill_text, re.M)
    name = skill_h1.group(1).strip().strip("«»\"'") if skill_h1 else None
    activates = "активируй навык" in command_text.lower()
    names_it = bool(name) and name.lower() in command_text.lower()
    if not activates:
        findings.append("run-agent.md: команда не активирует навык — нет блока «Активируй навык …»")
    elif not names_it and "skills/00-agent-skill/SKILL.md" not in command_text:
        findings.append(f"run-agent.md: команда не называет навык по имени «{name}»")
    if "Контур" not in command_text:
        findings.append("run-agent.md: не задан параметр «Контур»")

    for word in ("интеграция подключена", "write-back выполнен", "данные записаны в"):
        if word in low:
            notes.append(f"проверь формулировку: «{word}» может читаться как обещание реальной интеграции")
    return {"status": "FAIL" if findings else "PASS", "findings": findings,
            "missing_sections": missing_sections, "notes": notes}


def check_tree(root: Path) -> list:
    problems = []
    allowed = {Path("commands/run-agent.md"), Path("skills/00-agent-skill/SKILL.md")}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_dir():
            if rel.parts[0] not in ("commands", "skills"):
                problems.append(f"лишний каталог: {rel}")
            elif rel.parts[0] == "skills" and len(rel.parts) == 2 and rel.parts[1] != "00-agent-skill":
                problems.append(f"лишний вложенный каталог: {rel}")
            elif len(rel.parts) > 2:
                problems.append(f"лишний вложенный каталог: {rel}")
            continue
        if rel in allowed:
            continue
        if p.name.lower() in FORBIDDEN_NAMES or any(rel.parts[0].lower().startswith(x)
                                                    for x in FORBIDDEN_DIR_PREFIXES):
            problems.append(f"запрещённый файл: {rel}")
        else:
            problems.append(f"лишний файл: {rel}")
    for need in allowed:
        if not (root / need).is_file():
            problems.append(f"нет обязательного файла: {need}")
    return problems


def spec_link_problems(skill_text: str, spec_path: Path | None) -> list:
    """Пакет ссылается на спецификацию отпечатком; если дана спецификация —
    отпечаток обязан совпадать, иначе пакет собран не из неё."""
    if spec_path is None:
        return []
    fm = frontmatter(nfc(skill_text)) or ""
    m = re.search(r"^\s*спецификация:\s*(\S+)\s*·\s*(\S+)\s*·\s*([0-9a-f]{8})", fm, re.M | re.I)
    if not m:
        return ["SKILL.md: ссылка на спецификацию не разобрана"]
    spec = parse_spec(spec_path)
    problems = []
    if m.group(1) != spec.slug:
        problems.append(f"SKILL.md: спецификация id «{m.group(1)}» ≠ «{spec.slug}»")
    if m.group(3) != spec.fp:
        problems.append(f"SKILL.md: отпечаток спецификации {m.group(3)} ≠ текущему {spec.fp} — "
                        "спецификация менялась после сборки; пересобери пакет")
    return problems


def audit_package(package: Path, spec_path: Path | None = None) -> dict:
    if not package.is_dir():
        print(f"ОШИБКА ВХОДА: папка пакета не найдена: {package}", file=sys.stderr)
        sys.exit(EXIT_INPUT)
    tree = check_tree(package)
    skill_p = package / "skills" / "00-agent-skill" / "SKILL.md"
    cmd_p = package / "commands" / "run-agent.md"
    skill_text = skill_p.read_text(encoding="utf-8") if skill_p.is_file() else ""
    cmd_text = cmd_p.read_text(encoding="utf-8") if cmd_p.is_file() else ""
    res = audit_texts(skill_text, cmd_text, package.name)
    res["findings"] = tree + res["findings"] + spec_link_problems(skill_text, spec_path)
    res["status"] = "FAIL" if res["findings"] else "PASS"
    res["package"] = str(package)
    return res


def render(res: dict) -> str:
    out = [f"АУДИТ ПАКЕТА · {res['package']}", f"audit_status: {res['status']}"]
    if res["findings"]:
        out.append("critical_findings:")
        out += [f"  - {f}" for f in res["findings"]]
    if res["notes"]:
        out.append("notes:")
        out += [f"  - {n}" for n in res["notes"]]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Структурный аудит пакета агента GigaCowork")
    ap.add_argument("--package", required=True, help="папка <agent-slug>")
    ap.add_argument("--spec", help="AGENT_SPEC.md — сверить отпечаток")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    res = audit_package(Path(args.package), Path(args.spec) if args.spec else None)
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.json else render(res))
    return EXIT_OK if res["status"] == "PASS" else EXIT_RED


if __name__ == "__main__":
    sys.exit(main())
