/**
 * docx_helpers.js — переиспользуемые функции сборки сценария GigaCowork-агента.
 *
 * Использование в per-проектном скрипте сборки:
 *
 *   const {
 *     Document, Packer, h1, h2, h3, h4, p, bullet, numbered, pageBreak,
 *     dataTable, metaTable, numbering, PAGE, MARGIN,
 *   } = require("./docx_helpers.js");
 *
 *   const children = [ ...h1("1. ..."), ... ];
 *   const doc = new Document({
 *     numbering,
 *     sections: [{ properties: { page: { size: PAGE, margin: MARGIN } }, children }],
 *   });
 *   Packer.toBuffer(doc).then(buf => fs.writeFileSync("out.docx", buf));
 *
 * Требует: npm install docx
 * Цвета/размеры/логика — см. references/design_system.md в этом скилле.
 * Точная структура документа, куда эти хелперы подставлять — см.
 * references/scenario_template.md.
 */

const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  WidthType, ShadingType, AlignmentType, PageBreak, LevelFormat,
} = require("docx");

const FONT = "Calibri";
const NAVY = "1F3864";
const GOLD = "B8860B";
const GREY = "666666";
const LIGHT = "F2F2F2";

const PAGE = { width: 11906, height: 16838 }; // A4
const MARGIN = { top: 1000, bottom: 1000, left: 1100, right: 1100 };

const numbering = {
  config: [
    {
      reference: "bullets",
      levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 420, hanging: 260 } } } }],
    },
    {
      reference: "numbered",
      levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 420, hanging: 260 } } } }],
    },
  ],
};

// ---------- заголовки ----------
function h1(text) {
  return new Paragraph({ spacing: { before: 320, after: 180 }, children: [new TextRun({ text, bold: true, size: 30, font: FONT, color: NAVY })] });
}
function h2(text) {
  return new Paragraph({ spacing: { before: 260, after: 140 }, children: [new TextRun({ text, bold: true, size: 25, font: FONT, color: NAVY })] });
}
function h3(text) {
  return new Paragraph({ spacing: { before: 220, after: 120 }, children: [new TextRun({ text, bold: true, size: 23, font: FONT, color: NAVY })] });
}
function h4(text) {
  return new Paragraph({ spacing: { before: 180, after: 100 }, children: [new TextRun({ text, bold: true, size: 21, font: FONT, color: GOLD })] });
}

// ---------- текст ----------
function p(text, o = {}) {
  return new Paragraph({
    spacing: { after: o.after ?? 140 },
    children: [new TextRun({ text, font: FONT, size: o.size ?? 21, italics: !!o.italics, bold: !!o.bold, color: o.color })],
  });
}
function bullet(text, o = {}) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { after: o.after ?? 100 },
    children: [new TextRun({ text, font: FONT, size: o.size ?? 21, bold: !!o.bold, italics: !!o.italics })],
  });
}
function numbered(text, o = {}) {
  return new Paragraph({
    numbering: { reference: "numbered", level: 0 },
    spacing: { after: o.after ?? 100 },
    children: [new TextRun({ text, font: FONT, size: o.size ?? 21 })],
  });
}
function pageBreak() {
  return new Paragraph({ children: [new PageBreak()] });
}

// ---------- таблицы ----------
function cell(text, o = {}) {
  return new TableCell({
    width: { size: o.width ?? 2000, type: WidthType.DXA },
    shading: o.header ? { type: ShadingType.CLEAR, fill: NAVY } : o.shade ? { type: ShadingType.CLEAR, fill: LIGHT } : undefined,
    verticalAlign: "center",
    margins: { top: 80, bottom: 80, left: 100, right: 100 },
    children: [new Paragraph({ children: [new TextRun({ text, font: FONT, size: o.size ?? 19, bold: !!o.header || !!o.bold, color: o.header ? "FFFFFF" : undefined })] })],
  });
}

/** Таблица данных с шапкой (заливка NAVY) и zebra-striping чётных строк. */
function dataTable(headers, rows, colWidths) {
  const total = colWidths.reduce((a, b) => a + b, 0);
  return new Table({
    width: { size: total, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [
      new TableRow({ tableHeader: true, children: headers.map((hh, i) => cell(hh, { header: true, width: colWidths[i] })) }),
      ...rows.map((r, ri) => new TableRow({ children: r.map((v, i) => cell(v, { width: colWidths[i], shade: ri % 2 === 1 })) })),
    ],
  });
}

/** Метатаблица «Параметр/Значение» — левая колонка всегда LIGHT + bold. */
function metaTable(rows) {
  return new Table({
    width: { size: 9026, type: WidthType.DXA },
    columnWidths: [2800, 6226],
    rows: rows.map(([label, value]) => new TableRow({ children: [cell(label, { width: 2800, shade: true, bold: true }), cell(value, { width: 6226 })] })),
  });
}

module.exports = {
  Document, Packer, Paragraph, TextRun,
  FONT, NAVY, GOLD, GREY, LIGHT, PAGE, MARGIN, numbering,
  h1, h2, h3, h4, p, bullet, numbered, pageBreak, cell, dataTable, metaTable,
};
