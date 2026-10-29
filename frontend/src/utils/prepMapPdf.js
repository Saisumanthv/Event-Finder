const PAGE = { width: 595.28, height: 841.89, margin: 48 };
const INK = [31, 41, 55];
const MUTED = [107, 114, 128];
const ACCENT = [79, 70, 229];

const REPLACEMENTS = [
  [/[‘’]/g, "'"],
  [/[“”]/g, '"'],
  [/[–—]/g, "-"],
  [/…/g, "..."],
  [/•/g, "-"],
];

// The built-in PDF fonts only cover Latin-1, so swap common symbols and drop the rest.
export function clean(text) {
  let result = String(text ?? "");
  for (const [pattern, replacement] of REPLACEMENTS) result = result.replace(pattern, replacement);
  return result.replace(/[^\x20-\x7E\xA0-\xFF]/g, "");
}

export function slug(text) {
  const base = clean(text)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return base.slice(0, 50) || "hackathon";
}

function metaLine(card) {
  const parts = [card.subtitle, card.category, card.date, card.mode, card.location];
  if (card.cost) parts.push(card.cost === "free" ? "Free" : "Paid");
  return parts.filter(Boolean).join("  |  ");
}

// `sections` is a list of { label, blurb, prep }: one per approach included in the PDF.
export async function buildPrepMapPdf(card, sections) {
  const { jsPDF } = await import("jspdf");
  const doc = new jsPDF({ unit: "pt", format: "a4" });
  const width = PAGE.width - PAGE.margin * 2;
  let y = PAGE.margin;

  function room(height) {
    if (y + height > PAGE.height - PAGE.margin) {
      doc.addPage();
      y = PAGE.margin;
    }
  }

  // Wraps at spaces, then hard-splits any line that is still too wide (long links, long words).
  function wrap(text, maxWidth) {
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

  // Writes wrapped text; `bullet` draws a dot with a hanging indent.
  function write(text, { size = 11, style = "normal", indent = 0, color = INK, after = 4, bullet = false } = {}) {
    doc.setFont("helvetica", style);
    doc.setFontSize(size);
    doc.setTextColor(...color);
    const lineHeight = size * 1.4;
    const textIndent = indent + (bullet ? 12 : 0);
    const lines = wrap(clean(text), width - textIndent);
    lines.forEach((line, i) => {
      room(lineHeight);
      if (bullet && i === 0) {
        doc.setFillColor(...color);
        doc.circle(PAGE.margin + indent + 3, y + size * 0.65, 1.6, "F");
      }
      doc.text(line, PAGE.margin + textIndent, y + size);
      y += lineHeight;
    });
    y += after;
  }

  write("PREP MAP", { size: 10, style: "bold", color: ACCENT, after: 2 });
  write(card.title, { size: 20, style: "bold", after: 4 });
  const meta = metaLine(card);
  if (meta) write(meta, { size: 10, color: MUTED, after: 2 });
  if (card.link) write(card.link, { size: 9, color: MUTED, after: 10 });

  sections.forEach(({ label, blurb, prep }) => {
    room(90);
    write(label, { size: 16, style: "bold", color: ACCENT, after: 1 });
    if (blurb) write(blurb, { size: 10, color: MUTED, after: 6 });
    if (prep.fit_note) write(`Note: ${prep.fit_note}`, { size: 10, style: "bold", color: ACCENT, after: 6 });
    if (prep.overview) write(prep.overview, { size: 11, after: 6 });
    if (prep.basis === "general" && !prep.fallback) {
      write("The listing gave few details, so this map is based on the field the title suggests.", {
        size: 10,
        style: "italic",
        color: MUTED,
        after: 6,
      });
    }

    prep.skills.forEach((skill, index) => {
      room(60);
      write(`${index + 1}. ${skill.name}`, { size: 13, style: "bold", after: 2 });
      if (skill.why) write(skill.why, { size: 10, style: "italic", color: MUTED, indent: 14, after: 4 });
      skill.topics.forEach((topic) => {
        room(36);
        write(topic.name, { size: 11, style: "bold", indent: 14, after: 1 });
        topic.subtopics.forEach((sub) => write(sub, { size: 10.5, indent: 26, bullet: true, after: 1 }));
        y += 4;
      });
      y += 8;
    });

    if (prep.skills.length === 0) write("No skills to show.", { color: MUTED });
    y += 12;
  });

  const total = doc.getNumberOfPages();
  for (let page = 1; page <= total; page += 1) {
    doc.setPage(page);
    doc.setFont("helvetica", "normal");
    doc.setFontSize(8.5);
    doc.setTextColor(...MUTED);
    const footerY = PAGE.height - 24;
    doc.text(
      "Event Finder - AI-generated study suggestions. Check the official page for tracks, rules and tools.",
      PAGE.margin,
      footerY,
    );
    doc.text(`Page ${page} of ${total}`, PAGE.width - PAGE.margin, footerY, { align: "right" });
  }

  return doc;
}

export async function downloadPrepMapPdf(card, sections) {
  const doc = await buildPrepMapPdf(card, sections);
  const suffix = sections.length === 1 && sections[0].key !== "fundamentals" ? `-${sections[0].key}` : "";
  doc.save(`prep-map-${slug(card.title)}${suffix}.pdf`);
}
