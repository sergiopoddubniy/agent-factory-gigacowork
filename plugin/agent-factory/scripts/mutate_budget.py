#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверки бюджета и разметки обязаны ломаться от подсева.

Код возврата: 0 — все мутации пойманы, 1 — есть выжившие или неприменённые.
Обоснование кода возврата и учёта неприменённых мутаций — в шапке
`mutate_landed.py`.
"""
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

S = pathlib.Path(__file__).resolve().parent.parent


def score(root: pathlib.Path):
    r = subprocess.run([sys.executable, str(root / "scripts/selftest_delivery.py")], cwd=root,
                       capture_output=True, text=True).stdout
    m = re.search(r"ИТОГО: (\d+)/(\d+)", r)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


base = score(S)
if base[0] is None:
    print("  selftest не дал вердикта — стенд не измерял ничего")
    sys.exit(1)
print(f"  без изменений: {base[0]}/{base[1]}\n")

MUT = [
    ("удалён инструмент замера",
     lambda d: (d / "scripts/token_budget.py").unlink()),
    ("незакрытый блок кода в SKILL.md",
     lambda d: (d / "SKILL.md").write_text(
         (d / "SKILL.md").read_text(encoding="utf-8") + "\n```text\nхвост\n",
         encoding="utf-8")),
    ("незакрытый блок в справочнике",
     lambda d: (d / "references/agent_package.md").write_text(
         (d / "references/agent_package.md").read_text(encoding="utf-8") + "\n```\n",
         encoding="utf-8")),
    ("язык переведён в совещательный",
     lambda d: _soften(d)),
]


def _soften(d: pathlib.Path) -> None:
    """Оркестратор фабрики — SKILL.md плюс references/pipelines/*.md (как в
    mutate_landed): смягчается всё вместе, иначе директивные пайплайны
    держат долю сильных маркеров выше порога и мутация выглядит пропущенной."""
    for p in [d / "SKILL.md"] + sorted((d / "references" / "pipelines").glob("*.md")):
        p.write_text(re.sub(r"обязательн|должен|нельзя|запрещ|всегда|никогда", "желательно",
                            p.read_text(encoding="utf-8")), encoding="utf-8")

missed, unapplied = [], []
for name, fn in MUT:
    tmp = pathlib.Path(tempfile.mkdtemp()) / "skill"
    shutil.copytree(S, tmp, ignore=lambda d, n: [x for x in n if x == "__pycache__"])
    before = {p.relative_to(tmp): p.read_bytes()
              for p in tmp.rglob("*") if p.is_file()}
    try:
        fn(tmp)
    except Exception as e:
        unapplied.append(name)
        print(f"  НЕ ПРИМЕНИЛАСЬ · {name} ({e})")
        shutil.rmtree(tmp.parent)
        continue
    after = {p.relative_to(tmp): p.read_bytes()
             for p in tmp.rglob("*") if p.is_file()}
    if before == after:
        # Мутация «выполнилась» без исключения, но ничего не изменила —
        # например, регулярное выражение не нашло, что заменять. Молча
        # засчитывать такое как проверенное нельзя.
        unapplied.append(name)
        print(f"  НЕ ПРИМЕНИЛАСЬ · {name} — содержимое не изменилось")
        shutil.rmtree(tmp.parent)
        continue
    got = score(tmp)
    ok = got[0] is not None and got[0] < base[0]
    if not ok:
        missed.append(name)
    print(f"  {'поймана     ' if ok else 'ПРОПУЩЕНА   '} · {name}: {got[0]}/{got[1]}")
    shutil.rmtree(tmp.parent)

print(f"\nвсего мутаций: {len(MUT)} · не ловится: {len(missed)} · "
      f"не применилось: {len(unapplied)}")
sys.exit(1 if (missed or unapplied) else 0)
