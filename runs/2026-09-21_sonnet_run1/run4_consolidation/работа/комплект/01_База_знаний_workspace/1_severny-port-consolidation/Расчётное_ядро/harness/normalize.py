"""Нормализация написания названий компаний периметра консолидации.

Реестр ВГО составляют люди по актам сверки (регламент §2.2), а не
выгружают из 1С — отсюда разнобой: кавычки то есть, то нет, форма
собственности иногда стоит после названия, а не перед ним (боль,
названная в брифе интейка: «сопоставляли глазами»).

Здесь — не поиск похожего кандидата (это Я15/agent_package.md и здесь
неприменимо: периметр закрыт и мал, три компании названы явно в
регламенте §1), а точное сопоставление по нормализованному ключу.
Компания, не сведённая ни к одному коду периметра, — это ошибка
источника (регламент §1: «до приказа любая четвёртая компания в
материалах — ошибка источника, а не расширение периметра»), и харнес
обязан остановиться, а не выбрать похожую.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_QUOTES_RE = re.compile(r"[«»\"'`‘’“”]")
_SPLIT_RE = re.compile(r"[\s\-]+")


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def name_key(raw: str, орг_формы: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(ключ_имени, формы) — ключ имени без формы собственности, формы
    отдельно (могут стоять и в начале, и в конце названия)."""
    s = _QUOTES_RE.sub("", _nfc(raw)).strip()
    tokens = [t for t in _SPLIT_RE.split(s) if t]
    forms_up = {f.upper() for f in орг_формы}
    forms = tuple(t.upper() for t in tokens if t.upper() in forms_up)
    name = tuple(t.lower() for t in tokens if t.upper() not in forms_up)
    return name, forms


@dataclass
class Периметр:
    """Периметр консолидации: код → (каноническое имя, ключ имени, роль, доля)."""
    записи: dict

    @classmethod
    def из_правил(cls, rules: dict) -> "Периметр":
        орг_формы = rules.get("орг_формы", [])
        записи = {}
        for p in rules["периметр"]:
            key, _ = name_key(p["каноническое_имя"], орг_формы)
            записи[p["код"]] = {
                "каноническое_имя": p["каноническое_имя"],
                "ключ": key,
                "роль": p["роль"],
                "доля_группы": p.get("доля_группы", 1.0),
            }
        return cls(записи)

    def опознать(self, raw: str, орг_формы: list[str]) -> str | None:
        """Код периметра по написанному названию, либо None — не опознано."""
        key, _ = name_key(raw, орг_формы)
        for код, зап in self.записи.items():
            if зап["ключ"] == key:
                return код
        return None

    def каноническое(self, код: str) -> str:
        return self.записи[код]["каноническое_имя"]

    def коды(self) -> list[str]:
        return list(self.записи.keys())
