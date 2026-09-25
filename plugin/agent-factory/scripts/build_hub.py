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

    python3 scripts/build_hub.py --out dist [--version V3.3] [--no-check] [--стоп-слова файл]

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
import zipfile
from datetime import date
from pathlib import Path

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


# --- команды в формате витрины ----------------------------------------------
def hub_command(src: Path) -> str:
    """Команда витрины: фронтматтер name (русское имя из заголовка «# /имя»),
    description (из summary), тело — как в плагине, без шапки версии."""
    s = src.read_text(encoding="utf-8")
    fm = re.match(r"^---\n(.*?)\n---\n", s, re.S)
    meta = {}
    if fm:
        for ln in fm.group(1).splitlines():
            k, _, v = ln.partition(":")
            meta[k.strip()] = v.strip()
        body = s[fm.end():]
    else:
        body = s
    title = re.search(r"^#\s*/?(\S+)\s*$", body, re.M)
    human = {"новый-агент": "Новый агент", "ворота": "Ворота готовности", "план-добора": "План добора",
             "собрать": "Сборка пакета", "комплект": "Комплект поставки", "приёмка": "Приёмка",
             "результат": "Результат", "портфель": "Портфель агентов", "пилот": "Пилот"}
    name = human.get(title.group(1) if title else "", (title.group(1) if title else src.stem).replace("-", " ").capitalize())
    body = re.sub(r"^#\s*/?\S+\s*\n+", "", body, count=1, flags=re.M)
    body = re.sub(r"^\*\*Версия:\*\*[^\n]*\n+", "", body, count=1, flags=re.M)
    desc = meta.get("summary", "").strip().rstrip(".")
    return f"---\nname: {name}\ndescription: {desc}\n---\n\n{body.strip()}\n"


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
            (dst / rel).mkdir(parents=True, exist_ok=True)
            continue
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        if p.suffix.lower() in TEXT_EXT and p.name != "hub_gate.py" and p.name != "build_hub.py":
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copyfile(p, dst / rel)
                continue
            t, st = scrub(text, code=p.suffix.lower() in (".py", ".js"))
            for k, v in st.items():
                stats[k] = stats.get(k, 0) + v
            if t != text:
                stats["файлов_изменено"] = stats.get("файлов_изменено", 0) + 1
            (dst / rel).write_text(t, encoding="utf-8")
        else:
            shutil.copyfile(p, dst / rel)


def fill(template: Path, **kw) -> str:
    s = template.read_text(encoding="utf-8")
    for k, v in kw.items():
        s = s.replace("{{" + k + "}}", str(v))
    return s


def write_zip(src: Path, zip_path: Path) -> str:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_dir():
                continue
            zi = zipfile.ZipInfo(str(Path(src.name) / p.relative_to(src)).replace("\\", "/"), date_time=ZIP_TS)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.flag_bits |= 0x800          # имена в UTF-8 — явно
            z.writestr(zi, p.read_bytes())
    return hashlib.sha256(zip_path.read_bytes()).hexdigest()


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


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
    version_num = re.sub(r"^V", "", version)
    out = Path(args.out).resolve()
    hub = out / "hub" / "agent-factory"
    if hub.parent.exists():
        shutil.rmtree(hub.parent)
    skill = hub / "skills" / "agent-factory"
    skill.mkdir(parents=True)

    stats: dict = {}
    copy_skill(skill, stats, args.with_examples)
    if not args.with_examples:
        # база шаблонов пуста — индекс пересобирается тем же скриптом, что и в репозитории
        run([sys.executable, "scripts/templates.py", "--индекс"], skill)
        skill_text = (skill / "SKILL.md").read_text(encoding="utf-8")
        skill_text = re.sub(r"^\| `examples/1c-dev-assistant/` \|[^\n]*\n", "", skill_text, flags=re.M)
        (skill / "SKILL.md").write_text(skill_text, encoding="utf-8")
    # копилки — пустые по политике: реестр и журнал остаются файлами, но без записей
    reg = skill / "references" / "knowledge" / "registry.yaml"
    if reg.exists():
        head = [ln for ln in reg.read_text(encoding="utf-8").splitlines() if ln.startswith("#")]
        reg.write_text("\n".join(head + ["[]"]) + "\n", encoding="utf-8")

    # команды витрины
    cmds_dir = hub / "commands"
    cmds_dir.mkdir()
    n_cmd = 0
    for c in sorted((PLUGIN / "commands").glob("*.md")):
        (cmds_dir / c.name).write_text(hub_command(c), encoding="utf-8")
        n_cmd += 1
    # обёртка
    tpl = PLUGIN / "templates" / "hub"
    refs = [p for p in (PLUGIN / "references").glob("*.md")] + [p for p in (PLUGIN / "references" / "pipelines").glob("*.md")]
    scripts = [p for p in (PLUGIN / "scripts").glob("*.py")]
    kw = dict(refs=len(refs), scripts=len(scripts), version=version, version_num=version_num, commands=n_cmd, date=args.date)
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
        r = run(gate_cmd, hub)
        gate_out = r.stdout
        gate_line = "ЗЕЛЁНЫЙ (0 нарушений)" if r.returncode == 0 else f"КРАСНЫЙ (код {r.returncode})"
        ok &= r.returncode == 0
        # selftest.py нужен пример (examples/), которого в витрине нет намеренно:
        # он запускается на источнике сборки — той же кодовой базе.
        r1 = run([sys.executable, "scripts/selftest.py"], skill if args.with_examples else PLUGIN)
        tail = (r1.stdout.strip().splitlines() or [""])[-1]
        self_line = f"{tail} (код {r1.returncode}; {'внутри витрины' if args.with_examples else 'на источнике сборки — в витрине нет примера'})"
        ok &= r1.returncode == 0
        r2 = run([sys.executable, "scripts/selftest_delivery.py"], skill)
        tail2 = (r2.stdout.strip().splitlines() or [""])[-1]
        deliv_line = f"{tail2} (код {r2.returncode})"
        ok &= r2.returncode == 0

    # --- архив ---
    zip_path = out / f"agent-factory_hub_{version}.zip"
    sha = write_zip(hub, zip_path)
    n_files = sum(1 for p in hub.rglob("*") if p.is_file())
    n_ref = stats.get("референсов_снято", 0)
    cyr = sum(1 for p in hub.rglob("*") if p.is_file() and re.search(r"[А-Яа-яЁё]", str(p.relative_to(hub))))
    report = [f"# Сборка витрины — Фабрика агентов GigaCowork {version}", "",
              f"- Дата: {args.date}", f"- Источник: `plugin/agent-factory` (SKILL.md {version})",
              f"- Раскладка: plugin.yaml · agents/agent-factory.md · commands/ ({n_cmd}) · skills/agent-factory/ · agent.html · README.md",
              f"- Файлов: {n_files}, из них с кириллицей в пути: {cyr} (архив с именами в UTF-8, собран Python zipfile)",
              f"- Справочников: {len(refs)} · скриптов: {len(scripts)}",
              f"- Вырезано провенанса: дат {stats.get('даты', 0)}, коротких дат у псевдонимов {stats.get('даты_у_псевдонима', 0)}, "
              f"адресов платформы {stats.get('платформа', 0)}; файлов изменено {stats.get('файлов_изменено', 0)}",
              f"- Референсы: {'перенесены (--с-примерами)' if args.with_examples else f'сняты — {n_ref} файлов примера и шаблонов; агент собирается с нуля по спецификации'}",
              f"- Копилки: реестр агентов пуст, журнал находок пуст, база шаблонов {'с примером' if args.with_examples else 'пуста'}",
              f"- Шлюз обезличивания (строгий): {gate_line}",
              f"- selftest внутри сборки: {self_line}",
              f"- selftest_delivery внутри сборки: {deliv_line}",
              f"- Архив: `{zip_path.name}` · SHA-256 {sha}", ""]
    if gate_out and "КРАСНЫЙ" in gate_line:
        report += ["## Нарушения шлюза", "", "```", gate_out.strip(), "```", ""]
    (out / "СБОРКА_ВИТРИНЫ.md").write_text("\n".join(report), encoding="utf-8")
    (out / "СБОРКА_ВИТРИНЫ.json").write_text(json.dumps({"version": version, "sha256": sha, "files": n_files,
                                                         "gate": gate_line, "selftest": self_line, "selftest_delivery": deliv_line,
                                                         "scrubbed": stats}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n".join(report))
    return EXIT_OK if ok else EXIT_RED


if __name__ == "__main__":
    sys.exit(main())
