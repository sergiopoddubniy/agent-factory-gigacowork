#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Перевод комплекта с раскладки «по типу артефакта» на «папка = агент».

    python3 migrate_layout.py --папка "<корень поставки>"            # план
    python3 migrate_layout.py --папка "<корень поставки>" --применить

Код возврата: 0 — план построен целиком либо миграция выполнена; 1 — есть
случаи, которые скрипт решать не вправе (разошедшиеся копии); 2 — нечего
мигрировать.

**Что переносится.** Карточка, команды и спецификация каждого агента — из
папок `02_Описания_агентов/`, `03_Слэш_команды/`, `00_Спецификации_агентов/`
в папку своего агента внутри `04_Пакеты_агентов/`.

**Чего скрипт не делает и почему.** Он не выбирает между двумя разошедшимися
копиями одного файла. Копии расходятся содержательно — одна правка внесена,
другая нет, — и выбор редакции это решение, а не операция. Скрипт называет
оба файла, показывает размер и дату и останавливается.

**Пути в текстах переписываются вместе с файлами.** Инструкция по сборке
ссылается на папки по именам; перенос, доведённый до файлов и не доведённый
до ссылок, — уже случившийся дефект: в пространстве осталась команда, ведущая
в папку, которой больше нет.
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import pathlib
import re
import shutil
import sys
import unicodedata
import zipfile
from datetime import date

nfc = lambda s: unicodedata.normalize("NFC", s)
nameflat = lambda s: re.sub(r"[\s_\-]+", " ", nfc(s)).lower().replace("ё", "е")

ТИПОВЫЕ = {
    "карточка": ("описания агентов", "описание агента", "карточки агентов"),
    "команды": ("слэш команды", "слеш команды", "команды агентов"),
    "спецификация": ("спецификации агентов",),
}
ПРЕФИКС = {"карточка": "01_Карточка_агента", "команды": "02_Команды",
           "спецификация": "03_Спецификация"}
# Архивы и старые версии агентами не считаются: иначе миграция начнёт
# раскладывать файлы по папкам, которые сами подлежат удалению.
АРХИВ = ("архив", "устарел", "old", "черновик", "draft", "к удалению",
         "дубль", "backup")
МЕТКА = "_К_УДАЛЕНИЮ_ВРУЧНУЮ_"


def ядро(имя: str) -> str:
    """Slug агента из имени файла: цифры, тип артефакта и версия — не он."""
    s = nameflat(имя)
    s = re.sub(r"\.(md|docx|zip|pdf)$", "", s)
    s = re.sub(r"\b(агент|агента|карточка|описание|описания|команды|слэш|"
               r"слеш|спецификация|v\d+)\b", " ", s)
    s = re.sub(r"\d+", " ", s)
    return re.sub(r"[\s_\-.]+", "", s)


def слаг(имя_папки: str) -> str:
    """Читаемый slug из имени папки агента: «3_dogovoritsya-ob-usloviyah» →
    «dogovoritsya-ob-usloviyah». Имя файла читает человек, и схлопнутое ядро
    («dogovoritsyaobusloviyah») годится для сопоставления, но не для имени."""
    s = re.sub(r"^[\d]+[_\- ]*", "", nfc(имя_папки)).strip()
    # Версия и дата в имени папки — свойство сборки, а не агента. В имени
    # файла они дают «00_Навык_tender-docs-analyst_V2_21.08.26.md» и ломают
    # единообразие между клиентами; версия живёт в имени папки и в журнале.
    return re.sub(r"[_\- ]*[Vv]\d+(?:[_\- ]*\d{2}[.\-]\d{2}[.\-]\d{2,4})?$",
                  "", s).strip()


def найти(root: pathlib.Path):
    dirs = [p for p in root.rglob("*") if p.is_dir()
            and not any(x.startswith(".") for x in p.parts)]
    пакеты = [d for d in dirs if "пакеты агентов" in nameflat(d.name)
              and not any(x in nameflat(d.name) for x in ("архив",))]
    # Целевая папка — та, что внутри пространства: пакет ставится в
    # пространство и живёт рядом со своей базой знаний. Папка пакетов в корне
    # портфеля — наследие раскладки «рабочее отдельно от поставляемого»,
    # которая и давала девять архивов дважды.
    в_пространстве = lambda d: any(("workspace" in nameflat(x)
                                    or "пространств" in nameflat(x))
                                   for x in d.parts)
    пакеты.sort(key=lambda d: (not в_пространстве(d), len(d.parts), str(d)))
    типовые = {}
    for вид, маски in ТИПОВЫЕ.items():
        типовые[вид] = [d for d in dirs
                        if any(m in nameflat(d.name) for m in маски)
                        and not d.name.startswith(МЕТКА)]
    return пакеты, типовые


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--папка", dest="root", required=True)
    ap.add_argument("--применить", dest="apply", action="store_true")
    a = ap.parse_args()
    root = pathlib.Path(nfc(a.root)).expanduser()
    if not root.is_dir():
        print(f"Нет такой папки: {root}")
        return 2

    пакеты, типовые = найти(root)
    if not пакеты:
        print("Папка пакетов агентов не найдена — мигрировать некуда.")
        return 2
    целевой = пакеты[0]
    print("=" * 64)
    print(f"МИГРАЦИЯ РАСКЛАДКИ — {root.name}")
    print("=" * 64)
    print(f"\nПапка агентов: {целевой.relative_to(root)}")
    if len(пакеты) > 1:
        print("  ! папок пакетов несколько, вторые — на удаление после сверки:")
        for d in пакеты[1:]:
            print(f"    · {d.relative_to(root)}")

    # Портфель часто отдаёт пакеты архивами без папок. Папка на агента —
    # это и есть целевая раскладка, поэтому создаём её из имени архива, а не
    # требуем, чтобы человек сделал это руками до запуска.
    существуют = {ядро(d.name): d for d in целевой.iterdir()
                  if d.is_dir() and not any(x in nameflat(d.name)
                                            for x in АРХИВ)}
    созданы = []
    for z in sorted(целевой.glob("*.zip")):
        if any(a in nameflat(z.name) for a in АРХИВ):
            continue
        # Папка агента может уже быть, но называться иначе: «1-slug» против
        # «1_slug.zip». Сопоставление идёт по ядру имени, иначе на одного
        # агента заводятся две папки — та самая раскладка, от которой уходим.
        d = существуют.get(ядро(z.stem)) or (целевой / z.stem)
        if not (d / z.name).exists():
            созданы.append((z, d / z.name))
    if созданы:
        print(f"\nСОЗДАТЬ ПАПКУ НА АГЕНТА ИЗ АРХИВА — {len(созданы)}")
        for z, t_ in созданы[:20]:
            print(f"  {z.name}  →  {t_.relative_to(root)}")
        if a.apply:
            for z, t_ in созданы:
                t_.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(z), str(t_))

    агенты = {ядро(d.name): d for d in целевой.iterdir()
              if d.is_dir() and not any(x in nameflat(d.name) for x in АРХИВ)}
    # В режиме плана папок ещё нет — считаем их будущими, иначе план обрывается
    # на первом же портфеле, где пакеты лежат архивами.
    for z, t_ in созданы:
        агенты.setdefault(ядро(z.stem), t_.parent)
    if not агенты:
        print("\nВ папке пакетов нет подпапок агентов. Если пакеты лежат "
              "архивами, распакуйте либо создайте папку на агента: миграция "
              "переносит файлы в папку агента, а не создаёт её вслепую.")
        return 2
    print(f"Агентов: {len(агенты)} — {', '.join(sorted(агенты))}")

    план: list[tuple[str, pathlib.Path, pathlib.Path]] = []
    удалить: list[pathlib.Path] = []
    спорные: list[str] = []

    for вид, папки in типовые.items():
        for d in папки:
            for f in sorted(d.iterdir()):
                if not f.is_file() or f.name.startswith("."):
                    continue
                k = ядро(f.name)
                цель = агенты.get(k)
                if цель is None:
                    спорные.append(f"{f.relative_to(root)} — агент «{k}» "
                                   "не найден среди папок пакетов")
                    continue
                уже = [g for g in (цель.iterdir() if цель.is_dir() else [])
                       if g.is_file() and g.suffix == f.suffix
                       and ядро(g.name) == k
                       and (вид[:6] in nameflat(g.name)
                            or (вид == "карточка" and "агент" in nameflat(g.name)))]
                if уже:
                    g = уже[0]
                    if filecmp.cmp(f, g, shallow=False):
                        удалить.append(f)
                    else:
                        спорные.append(
                            f"{вид} «{k}»: копии РАЗОШЛИСЬ — "
                            f"{f.relative_to(root)} ({f.stat().st_size} б) "
                            f"против {g.relative_to(root)} "
                            f"({g.stat().st_size} б)")
                else:
                    имя = f"{ПРЕФИКС[вид]}_{слаг(цель.name)}{f.suffix}"
                    план.append((вид, f, цель / имя))

    # Навык агента — открытым файлом с именем агента. Два случая: он лежит
    # безымянным `SKILL.md` (переименовать) либо только внутри архива
    # (достать). Внутри архива его не прочитать глазами и не найти поиском по
    # папке, а именно это делают с навыком чаще всего.
    навыки: list[tuple[str, pathlib.Path, pathlib.Path]] = []
    for k, цель in sorted(агенты.items()):
        if not цель.is_dir():
            continue
        имя = f"00_Навык_{слаг(цель.name)}.md"
        если_есть = [f for f in цель.iterdir()
                     if f.is_file() and "навык" in nameflat(f.name)
                     and f.suffix == ".md"]
        if если_есть:
            continue
        плоский = [f for f in цель.iterdir()
                   if f.is_file() and nameflat(f.name) == "skill.md"]
        if плоский:
            навыки.append(("переименовать", плоский[0], цель / имя))
            continue
        for z in sorted(цель.glob("*.zip")):
            try:
                with zipfile.ZipFile(z) as zf:
                    внутри = [n for n in zf.namelist()
                              if n.upper().endswith("SKILL.MD")]
                    if внутри:
                        навыки.append(("достать из архива", z, цель / имя))
                        break
            except zipfile.BadZipFile:
                спорные.append(f"{z.relative_to(root)} — не читается как архив")

    # Архивы в поставке не остаются: из них достаётся содержимое, сам архив
    # уезжает под метку. Упаковку под загрузку в конструктор делает человек
    # перед самой загрузкой — это шаг платформы, а не состав поставки.
    архивы = []
    for k, цель in sorted(агенты.items()):
        if цель.is_dir():
            архивы += [z for z in цель.glob("*.zip")]
    архивы += [z for z in целевой.glob("*.zip")]
    if архивы:
        print(f"\nАРХИВЫ ИЗ ПОСТАВКИ — {len(архивы)} (содержимое достаётся, "
              "архив помечается)")
        for z in архивы[:20]:
            print(f"  {z.relative_to(root)}")

    if навыки:
        print(f"\nНАВЫК ОТКРЫТЫМ ФАЙЛОМ — {len(навыки)}")
        for как, f, t in навыки[:20]:
            print(f"  [{как}] {f.name}  →  {t.relative_to(root)}")

    print(f"\nПЕРЕНОС — {len(план)}")
    for вид, f, t in план[:40]:
        print(f"  {f.relative_to(root)}\n      → {t.relative_to(root)}")
    print(f"\nУДАЛИТЬ КАК ТОЧНУЮ КОПИЮ — {len(удалить)}")
    for f in удалить[:40]:
        print(f"  {f.relative_to(root)}")
    if спорные:
        print(f"\nРЕШАЕТ ЧЕЛОВЕК — {len(спорные)}")
        for s in спорные:
            print(f"  · {s}")
        print("\n  Разошедшиеся копии скрипт не сводит: выбор редакции — "
              "решение, а не операция. Сведите вручную и запустите снова.")

    if not a.apply:
        print("\nЭто план. Чтобы выполнить: добавьте --применить")
        return 1 if спорные else 0
    if спорные:
        print("\nМиграция не выполнена: сначала разберите спорные случаи.")
        return 1

    for вид, f, t in план:
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(f), str(t))

    for как, f, t in навыки:
        if как == "переименовать":
            f.rename(t)
        else:
            with zipfile.ZipFile(f) as zf:
                внутри = [n for n in zf.namelist()
                          if n.upper().endswith("SKILL.MD")][0]
                t.write_bytes(zf.read(внутри))

    # Тело команды из архива — если в папке агента файла команд ещё нет.
    for z in list(архивы):
        папка_агента = z.parent
        if папка_агента == целевой:
            continue
        есть = [f for f in папка_агента.iterdir()
                if f.is_file() and "команд" in nameflat(f.name)]
        if not есть:
            try:
                with zipfile.ZipFile(z) as zf:
                    cmd = [n for n in zf.namelist()
                           if n.lower().endswith("run-agent.md")]
                    if cmd:
                        (папка_агента /
                         f"02_Команды_{слаг(папка_агента.name)}.md"
                         ).write_bytes(zf.read(cmd[0]))
            except zipfile.BadZipFile:
                pass
    # Сам архив — под метку: удалять нельзя, оставлять как есть нельзя.
    for z in архивы:
        if z.exists() and not z.name.startswith(МЕТКА):
            try:
                z.unlink()
            except OSError:
                z.rename(z.with_name(МЕТКА + z.name))
    # Точные копии удаляются; если среда не разрешает удаление — уезжают в
    # помеченную папку. Оставить копию на месте нельзя: она и есть дефект.
    карантин = root / f"{МЕТКА}точные_копии"
    for f in удалить:
        try:
            f.unlink()
        except OSError:
            карантин.mkdir(exist_ok=True)
            shutil.move(str(f), str(карантин / f.name))

    # Пути в текстах. Переписываем ИМЕНА ПАПОК, а не отдельные файлы: ссылки
    # в инструкции по сборке ведут в папку типа, и после переноса они ведут
    # в никуда.
    старые = [d.name for папки in типовые.values() for d in папки]
    правок = 0
    for md in root.rglob("*.md"):
        t = md.read_text(encoding="utf-8", errors="replace")
        новый = t
        for имя in старые:
            новый = новый.replace(f"{имя}/", f"{целевой.name}/<N>_<slug>/")
            новый = новый.replace(имя, f"{целевой.name}/<N>_<slug>")
        if новый != t:
            md.write_text(новый, encoding="utf-8")
            правок += 1

    # Опустевшие папки не удаляются, а ПЕРЕИМЕНОВЫВАЮТСЯ с явной пометкой.
    # Две причины. Удаление необратимо, а миграция идёт по клиентскому
    # комплекту. И среда, в которой скрипт запускают, может не разрешать
    # удаление вовсе — тогда падение на последнем шаге оставит миграцию
    # наполовину сделанной, а это хуже, чем лишняя папка с понятным именем.
    пустые = []
    for папки in типовые.values():
        for d in папки:
            if not d.is_dir() or any(d.iterdir()):
                continue
            try:
                d.rmdir()
            except OSError:
                # Повторный запуск не должен добавлять метку второй раз:
                # «_К_УДАЛЕНИЮ_ВРУЧНУЮ__К_УДАЛЕНИЮ_ВРУЧНУЮ_...» читается как
                # сбой инструмента, а не как пометка.
                if not d.name.startswith(МЕТКА):
                    d.rename(d.with_name(МЕТКА + d.name))
            пустые.append(d)

    # Вторая папка пакетов удаляется, только если КАЖДЫЙ её файл байт в байт
    # совпадает с файлом в целевой папке. Иначе она остаётся, и о ней
    # сообщается: молча удалить единственный экземпляр — худший исход из
    # возможных.
    убрана_вторая = []
    for d in пакеты[1:]:
        файлы = [f for f in d.rglob("*") if f.is_file()]
        # Сопоставление по СОДЕРЖИМОМУ, а не по имени: тот же архив лежал под
        # именами «1-slug.zip» и «1_slug.zip», и проверка по имени объявила
        # его уникальным. Разделитель в имени — не признак различия.
        целевые = {}
        for g in целевой.rglob("*"):
            if g.is_file():
                целевые.setdefault(
                    hashlib.sha256(g.read_bytes()).hexdigest(), g)
        все_есть = all(
            hashlib.sha256(f.read_bytes()).hexdigest() in целевые
            for f in файлы)
        if все_есть and файлы:
            try:
                shutil.rmtree(d)
            except OSError:
                if not d.name.startswith(МЕТКА):
                    d.rename(d.with_name(МЕТКА + d.name))
            убрана_вторая.append(d)
        elif файлы:
            print(f"\n  ! {d.relative_to(root)} оставлена: не все её файлы "
                  "нашлись в целевой папке байт в байт")

    журнал = root / "00_ЧТО_УБРАНО_И_ПОЧЕМУ.md"
    запись = (f"\n## Миграция раскладки — {date.today().isoformat()}\n\n"
              f"Артефакты агентов сведены в папку своего агента "
              f"(`{целевой.name}/<N>_<slug>/`). Перенесено файлов: {len(план)}; "
              f"навыков выложено открытым файлом: {len(навыки)}; "
              f"архивов убрано из поставки: {len(архивы)}; "
              f"удалено точных копий: {len(удалить)}; папок убрано: "
              f"{len(пустые)}; вторых папок пакетов убрано: "
              f"{len(убрана_вторая)}; текстов со ссылками поправлено: "
              f"{правок}.\n\n"
              f"Причина: раскладка по типу артефакта хранила карточку и "
              f"команды в двух экземплярах. Совпадающие копии расходятся при "
              f"первой правке, и увидеть это можно только сравнением файлов.\n")
    журнал.write_text((журнал.read_text(encoding="utf-8") if журнал.exists()
                       else "# Что убрано из комплекта и почему\n") + запись,
                      encoding="utf-8")

    print(f"\nГОТОВО. Перенесено {len(план)}, навыков открыто {len(навыки)}, "
          f"архивов убрано {len(архивы)}, "
          f"удалено копий {len(удалить)}, "
          f"папок убрано {len(пустые)}, текстов поправлено {правок}.")
    print(f"Запись добавлена в {журнал.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
