import { clean, slug } from "./prepMapPdf.js";

// The flow chart as a PDF: drawn as shapes on one tall page, so no box is ever split in two.
const W = 595.28;
const M = 40;
const CW = W - M * 2;
const INK = [17, 24, 39];
const MUTED = [107, 114, 128];
const ACCENT = [79, 70, 229];
const GREEN = [5, 150, 105];
const LINE = [129, 140, 248];

const isWebLink = (value) => /^https?:\/\//i.test(String(value ?? ""));

// Wraps at spaces, then hard-splits any line that is still too wide.
function wrap(doc, text, maxWidth) {
  const lines = [];
  for (let line of doc.splitTextToSize(text, maxWidth)) {
    while (line.length > 1 && doc.getTextWidth(line) > maxWidth) {
      let cut = line.length - 1;
      while (cut > 1 && doc.getTextWidth(line.slice(0, cut)) > maxWidth) cut -= 1;
      lines.push(line.slice(0, cut));
      line = line.slice(cut);
    }
    lines.push(line);
  }
  return lines;
}

// Lays the chart out (and draws it when `draw` is true). Returns the page height it needs.
function paint(doc, card, approach, prep, draw) {
  let y = M;

  const lines = (text, size, style, maxWidth) => {
    doc.setFont("helvetica", style);
    doc.setFontSize(size);
    return wrap(doc, clean(text), maxWidth);
  };
  const ink = (color) => doc.setTextColor(...color);

  function arrow() {
    const length = 26;
    if (draw) {
      doc.setDrawColor(...LINE);
      doc.setLineWidth(1.5);
      doc.line(W / 2, y, W / 2, y + length - 7);
      doc.setFillColor(...LINE);
      doc.triangle(W / 2 - 5, y + length - 9, W / 2 + 5, y + length - 9, W / 2, y + length, "F");
    }
    y += length;
  }

  function pill(text, color, url) {
    const arr = lines(text, 13, "bold", CW - 56);
    const h = arr.length * 17 + 22;
    const w = Math.min(CW, Math.max(...arr.map((l) => doc.getTextWidth(l))) + 56);
    if (draw) {
      const x = (W - w) / 2;
      const radius = Math.min(h / 2, 20);
      doc.setFillColor(...color);
      doc.roundedRect(x, y, w, h, radius, radius, "F");
      doc.setFont("helvetica", "bold");
      doc.setFontSize(13);
      ink([255, 255, 255]);
      arr.forEach((line, i) => doc.text(line, W / 2, y + 22 + i * 17, { align: "center" }));
      if (url) doc.link(x, y, w, h, { url });
    }
    y += h;
  }

  function skillBox(skill, index) {
    const x0 = M + 14;
    const innerW = CW - 28;
    const nameLines = lines(skill.name, 13, "bold", innerW - 40);
    const headH = Math.max(26, nameLines.length * 17);
    const whyLines = skill.why ? lines(skill.why, 9.5, "italic", innerW - 40) : [];
    const whyH = whyLines.length ? 4 + whyLines.length * 13 : 0;

    const colW = (innerW - 12) / 2;
    const topics = (skill.topics || []).map((topic) => {
      const title = lines(topic.name, 10.5, "bold", colW - 20);
      const bullets = (topic.subtopics || []).map((sub) => lines(sub, 9.5, "normal", colW - 34));
      const h = 10 + title.length * 14 + 4 + bullets.reduce((sum, b) => sum + b.length * 12.5 + 2, 0) + 10;
      return { title, bullets, h };
    });
    const rows = [];
    for (let i = 0; i < topics.length; i += 2) rows.push(topics.slice(i, i + 2));
    const rowHeights = rows.map((row) => Math.max(...row.map((t) => t.h)));
    const topicsH = rows.length ? 12 + rowHeights.reduce((a, b) => a + b, 0) + (rows.length - 1) * 10 : 0;
    const boxH = 14 + headH + whyH + topicsH + 14;

    if (draw) {
      doc.setDrawColor(199, 210, 254);
      doc.setLineWidth(1.5);
      doc.setFillColor(255, 255, 255);
      doc.roundedRect(M, y, CW, boxH, 12, 12, "FD");

      doc.setFillColor(...ACCENT);
      doc.circle(x0 + 12, y + 27, 12, "F");
      doc.setFont("helvetica", "bold");
      doc.setFontSize(11);
      ink([255, 255, 255]);
      doc.text(String(index + 1), x0 + 12, y + 31, { align: "center" });

      doc.setFont("helvetica", "bold");
      doc.setFontSize(13);
      ink(INK);
      nameLines.forEach((line, i) => doc.text(line, x0 + 34, y + 14 + 18 + i * 17));
      doc.setFont("helvetica", "italic");
      doc.setFontSize(9.5);
      ink(MUTED);
      whyLines.forEach((line, i) => doc.text(line, x0 + 34, y + 14 + headH + 14 + i * 13));

      let rowY = y + 14 + headH + whyH + 12;
      rows.forEach((row, r) => {
        row.forEach((topic, c) => {
          const bx = row.length === 1 ? x0 + (innerW - colW) / 2 : x0 + c * (colW + 12);
          doc.setFillColor(248, 250, 255);
          doc.setDrawColor(224, 231, 255);
          doc.setLineWidth(1);
          doc.roundedRect(bx, rowY, colW, rowHeights[r], 8, 8, "FD");
          let ty = rowY + 10;
          doc.setFont("helvetica", "bold");
          doc.setFontSize(10.5);
          ink([55, 48, 163]);
          topic.title.forEach((line) => {
            doc.text(line, bx + 10, ty + 10);
            ty += 14;
          });
          ty += 4;
          doc.setFont("helvetica", "normal");
          doc.setFontSize(9.5);
          topic.bullets.forEach((bullet) => {
            doc.setFillColor(55, 65, 81);
            doc.circle(bx + 14, ty + 6, 1.3, "F");
            ink([55, 65, 81]);
            bullet.forEach((line) => {
              doc.text(line, bx + 22, ty + 9);
              ty += 12.5;
            });
            ty += 2;
          });
        });
        rowY += rowHeights[r] + 10;
      });
    }
    y += boxH;
  }

  if (draw) {
    doc.setFont("helvetica", "bold");
    doc.setFontSize(9);
    ink(ACCENT);
    doc.text(clean(`PREP MAP FLOW CHART${approach ? ` - ${approach}` : ""}`).toUpperCase(), W / 2, y + 8, {
      align: "center",
    });
  }
  y += 24;
  pill(`Start: ${card.title}`, ACCENT);
  (prep.skills || []).forEach((skill, i) => {
    arrow();
    skillBox(skill, i);
  });
  arrow();
  pill("Ready to take part", GREEN, isWebLink(card.link) ? String(card.link) : "");

  y += 22;
  const note = lines(
    "Event Finder - AI-generated study suggestions. Check the official page for tracks, rules and tools.",
    8.5,
    "normal",
    CW,
  );
  if (draw) {
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8.5);
    ink(MUTED);
    note.forEach((line, i) => doc.text(line, W / 2, y + 8 + i * 12, { align: "center" }));
  }
  y += note.length * 12;
  return y + M;
}

export async function buildFlowChartPdf(card, approach, prep) {
  const { jsPDF } = await import("jspdf");
  // A first, invisible pass measures the content so the real page can be exactly as tall as needed.
  const height = paint(new jsPDF({ unit: "pt", format: [W, 3000] }), card, approach, prep, false);
  const doc = new jsPDF({ unit: "pt", format: [W, Math.min(Math.max(height, W + 1), 14000)] });
  paint(doc, card, approach, prep, true);
  return doc;
}

export const flowPdfName = (card) => `prep-map-flow-${slug(card.title)}.pdf`;
