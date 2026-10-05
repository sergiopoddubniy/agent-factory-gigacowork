#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Куда Фабрика пишет своё состояние: реестр агентов, журнал сканирований,
перенесённые находки.

    from workdir import state_dir, writable, where

**Зачем.** На платформе GigaCowork папка навыка и документы пространства
доступны программе только на чтение (`/workspace`, запись даёт OSError —
проба на стенде 05.10.2026). Писать можно в рабочую папку сессии
`/session`; её файлы пользователь видит в панели «Документы» справа в чате
и оттуда скачивает. Скрипт, который пишет рядом с собой, на платформе падает
посреди работы — и реестр, и находка теряются.

**Порядок выбора папки.**

1. `FACTORY_DATA` — явная папка: консультант, тесты, общий диск команды;
2. исходная папка, если в неё можно писать: репозиторий на компьютере
   консультанта — поведение прежнее, ничего не меняется;
3. `/session/фабрика/<вид>` — рабочая папка сессии на платформе.

Во втором и третьем случае содержимое источника копируется в новую папку
(только недостающие файлы), чтобы реестр не начинался с нуля и прежний
журнал не терялся.

Проверка «можно ли писать» не создаёт пробных файлов: удалить их может быть
нельзя, и они остались бы мусором в папке пользователя.
"""
from __future__ import annotations

import os
import pathlib
import shutil

SESSION = pathlib.Path(os.environ.get("FACTORY_SESSION", "/session"))
ПАПКА_СЕССИИ = "фабрика"


def _existing(p: pathlib.Path) -> pathlib.Path | None:
    for d in (p, *p.parents):
        if d.exists():
            return d
    return None


def writable(p) -> bool:
    """Можно ли писать в папку (или создать её): права и не только чтение."""
    if p is None:
        return False
    d = _existing(pathlib.Path(p))
    if d is None or not d.is_dir():
        return False
    if not os.access(d, os.W_OK | os.X_OK):
        return False
    try:
        if os.statvfs(d).f_flag & getattr(os, "ST_RDONLY", 1):
            return False
    except (AttributeError, OSError):
        pass
    return True


def seed(source, target: pathlib.Path, names=None) -> list[str]:
    """Скопировать из источника файлы, которых в целевой папке ещё нет."""
    copied = []
    if source is None:
        return copied
    source = pathlib.Path(source)
    if not source.is_dir() or source.resolve() == target.resolve():
        return copied
    for f in sorted(source.iterdir()):
        if not f.is_file() or (names and f.name not in names):
            continue
        dst = target / f.name
        if not dst.exists():
            shutil.copyfile(f, dst)
            copied.append(f.name)
    return copied


def state_dir(source, kind: str, names=None) -> pathlib.Path:
    """Папка, куда писать состояние вида `kind` («знания», «находки»).

    `source` — где состояние лежит в скилле; может быть None, если исходного
    файла нет вовсе. Возвращённая папка существует и доступна на запись.
    """
    env = os.environ.get("FACTORY_DATA")
    candidates = []
    if env:
        candidates.append(pathlib.Path(env) / kind)
    elif source is not None and writable(source):
        p = pathlib.Path(source)
        p.mkdir(parents=True, exist_ok=True)
        return p
    candidates += [SESSION / ПАПКА_СЕССИИ / kind,
                   pathlib.Path.cwd() / ПАПКА_СЕССИИ / kind]
    last = None
    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            last = e
            continue
        if writable(d):
            seed(source, d, names)
            return d
    raise OSError(f"нет папки, доступной на запись, для «{kind}»: {last}")


def read_dir(source, kind: str) -> pathlib.Path | None:
    """Откуда читать состояние: там, куда его писали в прошлый раз.

    Чтение не создаёт папок. Порядок тот же, что у `state_dir`: явная папка,
    исходная (если в неё пишут), рабочая папка сессии, если там уже есть
    файлы; иначе — источник как есть.
    """
    env = os.environ.get("FACTORY_DATA")
    if env:
        d = pathlib.Path(env) / kind
        return d if d.is_dir() and any(d.iterdir()) else (
            pathlib.Path(source) if source is not None else None)
    if source is not None and writable(source):
        return pathlib.Path(source)
    d = SESSION / ПАПКА_СЕССИИ / kind
    if d.is_dir() and any(d.iterdir()):
        return d
    return pathlib.Path(source) if source is not None else None


def read_path(path, kind: str) -> pathlib.Path:
    """Файл для чтения: копия, записанная в прошлый раз, иначе исходный."""
    path = pathlib.Path(path)
    d = read_dir(path.parent, kind)
    if d is not None and (d / path.name).exists():
        return d / path.name
    return path


def write_path(path, kind: str) -> pathlib.Path:
    """Файл для записи: исходный, если можно писать, иначе копия в рабочей папке."""
    path = pathlib.Path(path)
    return state_dir(path.parent, kind, (path.name,)) / path.name


def where(path: pathlib.Path, source) -> str:
    """Строка для пользователя: куда записано, если не в исходное место.

    `path` и `source` — одного рода: оба папки или оба файла.
    """
    path = pathlib.Path(path)
    if source is not None and path.resolve() == pathlib.Path(source).resolve():
        return ""
    if os.environ.get("FACTORY_DATA"):
        return f"Записано в {path} — папка задана переменной FACTORY_DATA."
    try:
        path.resolve().relative_to(SESSION.resolve())
    except ValueError:
        return f"Записано в {path}: исходная папка доступна только на чтение."
    return (f"Записано в рабочую папку сессии: {path}. Папка навыка "
            "доступна только на чтение; файлы видны в панели «Документы» "
            "справа в чате — скачайте их и передайте владельцу навыка.")


if __name__ == "__main__":                                     # pragma: no cover
    import sys
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
    d = state_dir(src, sys.argv[2] if len(sys.argv) > 2 else "проба")
    print(d)
    print(where(d, src) or "пишется в исходную папку")
