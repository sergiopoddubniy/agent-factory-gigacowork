#!/usr/bin/env python3
"""
xlsx_writer.py — минимальный писатель .xlsx на стандартной библиотеке.

Нужен двум элементам поставки, у которых форма — книга Excel, а содержание
задаётся спецификацией: расчётному шаблону (Я10, поставляется ПУСТЫМ) и
расчётной модели эффекта (П11: лист «Параметры» и лист «Расчёт» с формулами).
Проверка комплекта (`check_delivery.py`) открывает эти файлы через openpyxl
там, где он есть, и смотрит на имена листов и заполненность строк — поэтому
книга должна быть настоящей, а не переименованным csv.

    from xlsx_writer import Book
    b = Book()
    b.sheet("Параметры", [["Параметр", "Значение", "Единица"], ["часов до", 6, "ч"]])
    b.sheet("Расчёт", [["Показатель", "Формула"], ["Экономия", "=Параметры!B2-Параметры!B3"]])
    b.save("модель.xlsx")

Строка — список ячеек: str → текст, int/float → число, str, начинающаяся
с «=» → формула. Стилей нет намеренно: это рабочая модель, не презентация.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


class Book:
    def __init__(self) -> None:
        self.sheets: list[tuple[str, list[list]]] = []

    def sheet(self, name: str, rows: list[list]) -> None:
        self.sheets.append((name[:31], rows))

    @staticmethod
    def _sheet_xml(rows: list[list]) -> str:
        out = []
        for r, row in enumerate(rows, 1):
            cells = []
            for c, v in enumerate(row):
                ref = f"{_col(c)}{r}"
                if v is None or v == "":
                    continue
                if isinstance(v, bool):
                    cells.append(f'<c r="{ref}" t="b"><v>{int(v)}</v></c>')
                elif isinstance(v, (int, float)):
                    cells.append(f'<c r="{ref}"><v>{v}</v></c>')
                elif isinstance(v, str) and v.startswith("="):
                    cells.append(f'<c r="{ref}"><f>{escape(v[1:])}</f></c>')
                else:
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                                 f'{escape(str(v))}</t></is></c>')
            out.append(f'<row r="{r}">{"".join(cells)}</row>')
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetFormatPr defaultRowHeight="15"/><cols><col min="1" max="1" width="42" customWidth="1"/>'
                '<col min="2" max="6" width="22" customWidth="1"/></cols>'
                f'<sheetData>{"".join(out)}</sheetData></worksheet>')

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self.sheets:
            self.sheet("Лист1", [[""]])
        sheets_xml = "".join(
            f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, (n, _) in enumerate(self.sheets, 1))
        workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    f'<sheets>{sheets_xml}</sheets></workbook>')
        wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(self.sheets) + 1))
                   + f'<Relationship Id="rId{len(self.sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   '</Relationships>')
        styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                  '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                  '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
                  '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                  '<fill><patternFill patternType="gray125"/></fill></fills>'
                  '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                  '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                  '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
                  '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                  '</styleSheet>')
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                      for i in range(1, len(self.sheets) + 1))
            + '</Types>')
        rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                '</Relationships>')
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            parts = [("[Content_Types].xml", content_types), ("_rels/.rels", rels),
                     ("xl/workbook.xml", workbook), ("xl/_rels/workbook.xml.rels", wb_rels),
                     ("xl/styles.xml", styles)]
            parts += [(f"xl/worksheets/sheet{i}.xml", self._sheet_xml(rows))
                      for i, (_, rows) in enumerate(self.sheets, 1)]
            for name, data in parts:
                info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data.encode("utf-8"))
        return path


if __name__ == "__main__":
    b = Book()
    b.sheet("Параметры", [["Параметр", "Значение", "Единица"], ["часов на задачу до", 6, "ч"],
                          ["часов на задачу после", 2, "ч"], ["задач в месяц", 20, "шт"]])
    b.sheet("Расчёт", [["Показатель", "Значение"], ["Экономия часов в месяц",
                                                     "=(Параметры!B2-Параметры!B3)*Параметры!B4"]])
    p = b.save("/tmp/xlsx_writer_demo.xlsx")
    print(p)
