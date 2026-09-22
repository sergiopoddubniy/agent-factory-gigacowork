#!/usr/bin/env python3
"""
docx_writer.py — минимальный писатель .docx на стандартной библиотеке.

Зачем свой. Исходный скилл собирает клиентский docx через npm-пакет `docx`
(`docx_helpers.js`); в среде, где есть только Python (проверки на платформе,
ноутбук аналитика без Node), сценарий агента остаётся несобранным — а Я5
(спецификация .docx) обязателен в ядре поставки. Здесь ровно то, что нужно
`scenario_template.md`: заголовки h1–h4, абзацы, маркированные и нумерованные
списки, таблицы с шапкой и «зеброй», метатаблицы, разрыв страницы. Цвета,
шрифт и размеры — по `design_system.md`, один в один с docx_helpers.js.

    from docx_writer import Doc
    d = Doc()
    d.small("СБЕР · GIGACOWORK", color=GOLD); d.title("ИИ-агент …")
    d.h1("1. …"); d.p("текст"); d.bullets(["а", "б"])
    d.table(["Файл", "Содержание"], [["a.md", "…"]], widths=[3000, 6000])
    d.meta([("Платформа", "GigaCowork"), …]); d.page_break()
    d.save("файл.docx")

Ограничения названы честно: без картинок, без колонтитулов, без оглавления.
Документ открывается Word, LibreOffice и Google Docs.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

NAVY, GOLD, GREY, LIGHT, WHITE = "1F3864", "B8860B", "666666", "F2F2F2", "FFFFFF"
FONT = "Calibri"
SZ = {"title": 36, "h1": 30, "h2": 25, "h3": 23, "h4": 21, "p": 21, "cell": 20, "small": 18}
PAGE_W, PAGE_H = 11906, 16838          # A4, DXA
MARGIN_TB, MARGIN_LR = 1000, 1100
CONTENT_W = PAGE_W - 2 * MARGIN_LR      # 9706 DXA


def _run(text: str, *, bold=False, italic=False, color=None, size=None) -> str:
    rpr = [f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}" w:eastAsia="{FONT}"/>']
    if bold:
        rpr.append("<w:b/><w:bCs/>")
    if italic:
        rpr.append("<w:i/><w:iCs/>")
    if color:
        rpr.append(f'<w:color w:val="{color}"/>')
    if size:
        rpr.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    out = []
    parts = text.split("\n")
    for i, part in enumerate(parts):
        if i:
            out.append("<w:br/>")
        out.append(f'<w:t xml:space="preserve">{escape(part)}</w:t>')
    return f"<w:r><w:rPr>{''.join(rpr)}</w:rPr>{''.join(out)}</w:r>"


def _para(runs: str, *, style=None, align=None, spacing_after=120, numbering=None,
          keep_next=False, page_break_before=False) -> str:
    ppr = []
    if style:
        ppr.append(f'<w:pStyle w:val="{style}"/>')
    if keep_next:
        ppr.append("<w:keepNext/>")
    if page_break_before:
        ppr.append("<w:pageBreakBefore/>")
    if numbering is not None:
        num_id, lvl = numbering
        ppr.append(f'<w:numPr><w:ilvl w:val="{lvl}"/><w:numId w:val="{num_id}"/></w:numPr>')
    ppr.append(f'<w:spacing w:after="{spacing_after}" w:line="276" w:lineRule="auto"/>')
    if align:
        ppr.append(f'<w:jc w:val="{align}"/>')
    return f"<w:p><w:pPr>{''.join(ppr)}</w:pPr>{runs}</w:p>"


def _cell(content_xml: str, width: int, fill: str | None = None) -> str:
    tcpr = [f'<w:tcW w:w="{width}" w:type="dxa"/>']
    if fill:
        tcpr.append(f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>')
    tcpr.append('<w:tcMar><w:top w:w="60" w:type="dxa"/><w:left w:w="100" w:type="dxa"/>'
                '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="100" w:type="dxa"/></w:tcMar>')
    return f"<w:tc><w:tcPr>{''.join(tcpr)}</w:tcPr>{content_xml}</w:tc>"


class Doc:
    def __init__(self) -> None:
        self.body: list[str] = []
        self._num_seq = 1  # нумерованные списки: каждому свой numId (перезапуск нумерации)
        self._numbered_ids: list[int] = []

    # ---- текст -------------------------------------------------------------
    def small(self, text: str, color: str = GOLD, bold: bool = True, italic: bool = False) -> None:
        self.body.append(_para(_run(text, bold=bold, italic=italic, color=color, size=SZ["small"]),
                               spacing_after=60))

    def title(self, text: str) -> None:
        self.body.append(_para(_run(text, bold=True, color=NAVY, size=SZ["title"]), spacing_after=240))

    def subtitle(self, text: str) -> None:
        self.body.append(_para(_run(text, italic=True, color=GREY, size=SZ["p"]), spacing_after=200))

    def h1(self, text: str, page_break: bool = False) -> None:
        self.body.append(_para(_run(text, bold=True, color=NAVY, size=SZ["h1"]), style="Heading1",
                               spacing_after=160, keep_next=True, page_break_before=page_break))

    def h2(self, text: str) -> None:
        self.body.append(_para(_run(text, bold=True, color=NAVY, size=SZ["h2"]), style="Heading2",
                               spacing_after=120, keep_next=True))

    def h3(self, text: str) -> None:
        self.body.append(_para(_run(text, bold=True, color=NAVY, size=SZ["h3"]), style="Heading3",
                               spacing_after=100, keep_next=True))

    def h4(self, text: str) -> None:
        self.body.append(_para(_run(text, bold=True, color=GOLD, size=SZ["h4"]), style="Heading4",
                               spacing_after=80, keep_next=True))

    def p(self, text: str, *, bold=False, italic=False, color=None) -> None:
        self.body.append(_para(_run(text, bold=bold, italic=italic, color=color, size=SZ["p"])))

    def rich(self, parts: list[tuple[str, dict]]) -> None:
        """Абзац из фрагментов: [("жирное", {"bold": True}), (" обычное", {})]."""
        self.body.append(_para("".join(_run(t, size=SZ["p"], **kw) for t, kw in parts)))

    def bullets(self, items: list[str], bold_lead: bool = False) -> None:
        for it in items:
            if bold_lead and " — " in it:
                lead, rest = it.split(" — ", 1)
                runs = _run(lead, bold=True, size=SZ["p"]) + _run(" — " + rest, size=SZ["p"])
            else:
                runs = _run(it, bold=bold_lead and " — " not in it, size=SZ["p"])
            self.body.append(_para(runs, numbering=(1, 0), spacing_after=60))

    def numbered(self, items: list[str]) -> None:
        self._num_seq += 1
        nid = self._num_seq
        self._numbered_ids.append(nid)
        for it in items:
            self.body.append(_para(_run(it, size=SZ["p"]), numbering=(nid, 0), spacing_after=60))

    def page_break(self) -> None:
        self.body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    # ---- таблицы -----------------------------------------------------------
    def _widths(self, n: int, widths: list[int] | None) -> list[int]:
        if widths and len(widths) == n:
            return widths
        base = CONTENT_W // n
        return [base] * (n - 1) + [CONTENT_W - base * (n - 1)]

    def table(self, header: list[str], rows: list[list[str]], widths: list[int] | None = None,
              zebra: bool = True) -> None:
        w = self._widths(len(header), widths)
        grid = "".join(f'<w:gridCol w:w="{x}"/>' for x in w)
        tblpr = (f'<w:tblPr><w:tblW w:w="{sum(w)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
                 '<w:tblBorders>' + "".join(
                     f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
                     for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
                 + '</w:tblBorders></w:tblPr>')
        out = [f"<w:tbl>{tblpr}<w:tblGrid>{grid}</w:tblGrid>"]
        out.append("<w:tr><w:trPr><w:tblHeader/></w:trPr>" + "".join(
            _cell(_para(_run(h, bold=True, color=WHITE, size=SZ["cell"]), spacing_after=0), w[i], NAVY)
            for i, h in enumerate(header)) + "</w:tr>")
        for r, row in enumerate(rows):
            fill = LIGHT if (zebra and r % 2 == 1) else None
            cells = list(row) + [""] * (len(header) - len(row))
            out.append("<w:tr>" + "".join(
                _cell(_para(_run(str(c), size=SZ["cell"]), spacing_after=0), w[i], fill)
                for i, c in enumerate(cells[:len(header)])) + "</w:tr>")
        out.append("</w:tbl>")
        self.body.append("".join(out))
        self.body.append(_para("", spacing_after=120))

    def meta(self, pairs: list[tuple[str, str]], widths: list[int] | None = None) -> None:
        """Метатаблица Параметр/Значение: левая колонка LIGHT + bold."""
        w = self._widths(2, widths or [3000, CONTENT_W - 3000])
        grid = "".join(f'<w:gridCol w:w="{x}"/>' for x in w)
        tblpr = (f'<w:tblPr><w:tblW w:w="{sum(w)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
                 '<w:tblBorders>' + "".join(
                     f'<w:{s} w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
                     for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
                 + '</w:tblBorders></w:tblPr>')
        out = [f"<w:tbl>{tblpr}<w:tblGrid>{grid}</w:tblGrid>"]
        for k, v in pairs:
            out.append("<w:tr>"
                       + _cell(_para(_run(k, bold=True, size=SZ["cell"]), spacing_after=0), w[0], LIGHT)
                       + _cell(_para(_run(str(v), size=SZ["cell"]), spacing_after=0), w[1])
                       + "</w:tr>")
        out.append("</w:tbl>")
        self.body.append("".join(out))
        self.body.append(_para("", spacing_after=120))

    # ---- сборка ------------------------------------------------------------
    def _document_xml(self) -> str:
        sect = (f'<w:sectPr><w:pgSz w:w="{PAGE_W}" w:h="{PAGE_H}"/>'
                f'<w:pgMar w:top="{MARGIN_TB}" w:right="{MARGIN_LR}" w:bottom="{MARGIN_TB}" '
                f'w:left="{MARGIN_LR}" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                f"<w:body>{''.join(self.body)}{sect}</w:body></w:document>")

    def _numbering_xml(self) -> str:
        abstract_bullet = ('<w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>'
                           '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
                           '<w:lvlText w:val="•"/><w:lvlJc w:val="left"/>'
                           '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr>'
                           '<w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/></w:rPr></w:lvl>'
                           '</w:abstractNum>')
        abstract_num = ('<w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="hybridMultilevel"/>'
                        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
                        '<w:lvlText w:val="%1."/><w:lvlJc w:val="left"/>'
                        '<w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl>'
                        '</w:abstractNum>')
        nums = ['<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>']
        for nid in self._numbered_ids:
            nums.append(f'<w:num w:numId="{nid}"><w:abstractNumId w:val="1"/>'
                        f'<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>')
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                + abstract_bullet + abstract_num + "".join(nums) + "</w:numbering>")

    @staticmethod
    def _styles_xml() -> str:
        def style(sid, name, size, color, bold=True, outline=None):
            ol = f'<w:outlineLvl w:val="{outline}"/>' if outline is not None else ""
            return (f'<w:style w:type="paragraph" w:styleId="{sid}"><w:name w:val="{name}"/>'
                    f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/>'
                    f'<w:pPr><w:keepNext/>{ol}<w:spacing w:before="200" w:after="120"/></w:pPr>'
                    f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}"/>{"<w:b/>" if bold else ""}'
                    f'<w:color w:val="{color}"/><w:sz w:val="{size}"/></w:rPr></w:style>')
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:docDefaults><w:rPrDefault><w:rPr>'
                f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}" w:eastAsia="{FONT}"/>'
                f'<w:sz w:val="{SZ["p"]}"/><w:szCs w:val="{SZ["p"]}"/><w:lang w:val="ru-RU"/>'
                '</w:rPr></w:rPrDefault><w:pPrDefault><w:pPr>'
                '<w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault>'
                '</w:docDefaults>'
                '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
                '<w:qFormat/></w:style>'
                + style("Heading1", "heading 1", SZ["h1"], NAVY, outline=0)
                + style("Heading2", "heading 2", SZ["h2"], NAVY, outline=1)
                + style("Heading3", "heading 3", SZ["h3"], NAVY, outline=2)
                + style("Heading4", "heading 4", SZ["h4"], GOLD, outline=3)
                + '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/>'
                  '<w:tblPr><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/>'
                  '<w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar>'
                  '</w:tblPr></w:style>'
                '</w:styles>')

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            '</Types>')
        rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
                '</Relationships>')
        doc_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
                    '</Relationships>')
        core = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
                'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                '<dc:title>Сценарий агента GigaCowork</dc:title><dc:creator>Фабрика агентов GigaCowork</dc:creator>'
                '</cp:coreProperties>')
        app = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
               'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
               '<Application>Фабрика агентов GigaCowork</Application></Properties>')
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in (("[Content_Types].xml", content_types), ("_rels/.rels", rels),
                               ("word/document.xml", self._document_xml()),
                               ("word/styles.xml", self._styles_xml()),
                               ("word/numbering.xml", self._numbering_xml()),
                               ("word/_rels/document.xml.rels", doc_rels),
                               ("docProps/core.xml", core), ("docProps/app.xml", app)):
                info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data.encode("utf-8"))
        return path


def docx_text(path: str | Path) -> str:
    """Текст документа без разметки — для проверок и селфтеста."""
    import re
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    xml = xml.replace("</w:p>", "\n").replace("<w:br/>", "\n")
    return re.sub(r"<[^>]+>", "", xml)


if __name__ == "__main__":
    d = Doc()
    d.small("СБЕР · GIGACOWORK")
    d.subtitle("Сценарий AI-агента")
    d.title("ИИ-агент проверки — Пример")
    d.meta([("Платформа", "GigaCowork"), ("Дата", "сентябрь 2026")])
    d.h1("1. Раздел")
    d.p("Абзац текста.")
    d.bullets(["первый — пояснение", "второй"], bold_lead=True)
    d.numbered(["шаг один", "шаг два"])
    d.table(["Файл", "Содержание"], [["a.md", "описание"], ["b.md", "ещё"]], widths=[3000, 6706])
    out = d.save("/tmp/docx_writer_demo.docx")
    print(out, len(docx_text(out)))
