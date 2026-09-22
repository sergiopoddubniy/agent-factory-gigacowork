#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Каждая правка из блока LANDED обязана роняться при её удалении.
Иначе блок — список благих намерений, а не проверка.

Код возврата: 0 — все мутации пойманы, 1 — есть выжившие или неприменённые.

**Про код возврата отдельно.** До 26.08 стенд печатал «не ловится: N» и
всегда возвращал 0. Ворота, которые всегда открыты, — это не ворота: любой
внешний запуск (регулярная задача, CI, чужой прогон) видел успех при
выживших мутантах. Нашла это ежедневная задача, сравнив поведение с
`token_budget.py` и `audit_findings.py`, которые на красном честно
возвращают 1.

Второе оттуда же: мутация, которая **не применилась**, раньше молча
выбрасывалась — знаменатель уменьшался, доля отлова росла. Теперь
неприменённая мутация считается провалом стенда: она означает, что игла
разошлась с текстом, то есть проверка вообще не испытана.
"""
import importlib.util
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

# Корень — от расположения файла, а не абсолютным путём. Прибитый путь
# песочницы пережил саму сессию: стенд падал до первой мутации у всех, кроме
# автора, и выглядел это как «скрипт сломан», а не «путь чужой».
S = pathlib.Path(__file__).resolve().parent.parent


def score(root: pathlib.Path):
    # Только блок «landed»: мутация правит справочник, и ловит её именно он.
    # Полный selftest вырос до 284 проверок с подпроцессами на каждый комплект,
    # и стенд из семидесяти мутаций стал идти дольше получаса. Стенд, который
    # не запускают, не отличается от отсутствующего — а «не ловится: 0» перед
    # каждым сохранением обязано быть посильным.
    r = subprocess.run([sys.executable, str(root / "scripts/selftest_delivery.py"),
                        "--only", "landed"], cwd=root,
                       capture_output=True, text=True).stdout
    m = re.search(r"ИТОГО: (\d+)/(\d+)", r)
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


spec = importlib.util.spec_from_file_location("st", S / "scripts/selftest_delivery.py")
st = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(st)
except SystemExit:
    pass
LANDED = st.LANDED

base = score(S)
if base[0] is None:
    print("  selftest не дал вердикта — стенд не измерял ничего")
    sys.exit(1)
print(f"  скилл без изменений: {base[0]}/{base[1]}\n")

missed, unapplied = [], []
for needle, rel, why in LANDED:
    tmp = pathlib.Path(tempfile.mkdtemp()) / "skill"
    shutil.copytree(S, tmp, ignore=lambda d, n: [x for x in n if x == "__pycache__"])
    # Оркестратор фабрики — SKILL.md плюс references/pipelines/*.md; игла с
    # адресом SKILL.md вырезается из всех этих файлов сразу, иначе копия в
    # пайплайне держит проверку зелёной, а мутация выглядит пропущенной.
    targets = [tmp / rel]
    if rel == "SKILL.md":
        targets += sorted((tmp / "references" / "pipelines").glob("*.md"))
    # Проверка сравнивает нормализованный текст, поэтому игла может быть
    # перенесена по строкам. Дословная замена такую не находит.
    rx = re.compile(r"\s+".join(re.escape(w) for w in needle.split()))
    # Заменяем ВСЕ вхождения: правило может быть повторено в файле, и
    # удаление первого оставляет второе — проверка справедливо остаётся
    # зелёной, а мутация выглядит пропущенной.
    changed = False
    for p in targets:
        o = p.read_text(encoding="utf-8")
        n = rx.sub("—", o)
        if n != o:
            p.write_text(n, encoding="utf-8")
            changed = True
    if not changed:
        unapplied.append((rel, needle))
        print(f"  НЕ ПРИМЕНИЛАСЬ · {rel.split('/')[-1]}: «{needle[:42]}» "
              f"— игла разошлась с текстом, проверка не испытана")
        shutil.rmtree(tmp.parent)
        continue
    got = score(tmp)
    ok = got[0] is not None and got[0] < base[0]
    if not ok:
        missed.append((rel, needle))
    print(f"  {'поймана     ' if ok else 'ПРОПУЩЕНА   '} · "
          f"{rel.split('/')[-1]}: «{needle[:42]}»")
    shutil.rmtree(tmp.parent)

print(f"\nвсего мутаций: {len(LANDED)} · не ловится: {len(missed)} · "
      f"не применилось: {len(unapplied)}")
if missed or unapplied:
    print("Стенд провален. Выживший мутант означает, что проверки на эту "
          "правку фактически нет; неприменённая мутация — что её не испытали.")
sys.exit(1 if (missed or unapplied) else 0)
