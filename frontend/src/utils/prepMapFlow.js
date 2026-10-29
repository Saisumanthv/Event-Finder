// Opens the skills of a Prep Map as a flow chart in a new browser tab.
// The page is built here as one self-contained HTML document, so it needs no route or library.
import { buildFlowChartPdf, flowPdfName } from "./prepMapFlowPdf.js";

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const STYLE = `
  * { box-sizing: border-box; }
  body { margin: 0; padding: 32px 16px 64px; background: #f4f6fb; color: #111827;
    font-family: Inter, "Segoe UI", system-ui, sans-serif; }
  .bar { display: flex; justify-content: center; margin-bottom: 20px; }
  .bar a { padding: 8px 18px; border: 1px solid #c7d2fe; border-radius: 10px; background: #eef2ff;
    color: #4338ca; font: inherit; font-size: 14px; font-weight: 600; text-decoration: none; cursor: pointer; }
  .bar a.wait { opacity: .6; cursor: progress; pointer-events: none; }
  .flow { display: flex; flex-direction: column; align-items: center; max-width: 1000px; margin: 0 auto; }
  .kicker { margin: 0; color: #4f46e5; font-size: 12px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
  .terminal { padding: 14px 28px; border-radius: 999px; background: linear-gradient(135deg, #4f46e5, #6366f1);
    color: #fff; font-weight: 700; text-align: center; max-width: 100%; }
  .terminal.end { background: linear-gradient(135deg, #059669, #10b981); }
  a.terminal { text-decoration: none; transition: transform .15s, box-shadow .15s; }
  a.terminal:hover { transform: translateY(-2px); box-shadow: 0 6px 16px rgba(5, 150, 105, .35); }
  .arrow { position: relative; width: 2px; height: 34px; background: #818cf8; margin: 0 auto; }
  .arrow::after { content: ""; position: absolute; bottom: -1px; left: -5px; border: 6px solid transparent;
    border-top: 9px solid #818cf8; border-bottom: 0; }
  .skill { width: 100%; padding: 18px; border: 2px solid #c7d2fe; border-radius: 16px; background: #fff;
    box-shadow: 0 4px 14px rgba(79, 70, 229, .08); }
  .skill-head { display: flex; align-items: center; gap: 12px; }
  .num { display: grid; flex: none; place-items: center; width: 32px; height: 32px; border-radius: 50%;
    background: #4f46e5; color: #fff; font-weight: 700; }
  .skill h2 { margin: 0; font-size: 18px; }
  .why { margin: 8px 0 0 44px; color: #6b7280; font-size: 14px; }
  .branch { width: 2px; height: 16px; margin: 12px auto 0; background: #c7d2fe; }
  .topics { display: flex; flex-wrap: wrap; justify-content: center; gap: 12px; padding-top: 12px;
    border-top: 2px solid #c7d2fe; }
  .topic { flex: 1 1 220px; max-width: 320px; padding: 12px 14px; border: 1px solid #e0e7ff; border-radius: 12px;
    background: #f8faff; }
  .topic h3 { margin: 0 0 6px; font-size: 15px; color: #3730a3; }
  .topic ul { margin: 0; padding-left: 18px; color: #374151; font-size: 13px; }
  .topic li { margin: 2px 0; }
  .note { margin: 28px auto 0; max-width: 700px; color: #6b7280; font-size: 12px; text-align: center; }
`;

// Only plain web links are allowed, so a bad value can never become a script link.
const safeLink = (value) => (/^https?:\/\//i.test(String(value ?? "")) ? String(value) : "");

export function buildFlowChartHtml(card, approach, prep) {
  const skills = prep.skills || [];
  const link = safeLink(card.link);
  const body = skills
    .map((skill, i) => {
      const topics = (skill.topics || [])
        .map(
          (t) => `<div class="topic"><h3>${escapeHtml(t.name)}</h3>${
            t.subtopics?.length ? `<ul>${t.subtopics.map((s) => `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : ""
          }</div>`,
        )
        .join("");
      return `<div class="arrow"></div>
        <section class="skill">
          <div class="skill-head"><span class="num">${i + 1}</span><h2>${escapeHtml(skill.name)}</h2></div>
          ${skill.why ? `<p class="why">${escapeHtml(skill.why)}</p>` : ""}
          ${topics ? `<div class="branch"></div><div class="topics">${topics}</div>` : ""}
        </section>`;
    })
    .join("");

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Flow chart - ${escapeHtml(card.title)}</title><style>${STYLE}</style></head>
<body>
  <div class="bar"><a id="pdf" class="wait" role="button">Preparing PDF...</a></div>
  <main class="flow">
    <p class="kicker">Prep Map flow chart${approach ? ` · ${escapeHtml(approach)}` : ""}</p>
    <div class="arrow" style="height:12px;background:none"></div>
    <div class="terminal">Start: ${escapeHtml(card.title)}</div>
    ${body}
    <div class="arrow"></div>
    ${
      link
        ? `<a class="terminal end" href="${escapeHtml(link)}" target="_blank" rel="noopener noreferrer">Ready to take part ↗</a>`
        : `<div class="terminal end">Ready to take part</div>`
    }
  </main>
  <p class="note">AI-generated study suggestions based on the listing. Check the official page for the tracks, rules and tools.</p>
</body></html>`;
}

// Returns false when the browser blocked the new tab.
export function openFlowChart(card, approach, prep) {
  const html = buildFlowChartHtml(card, approach, prep);
  const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  const tab = window.open(url, "_blank");
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  if (tab) attachPdf(tab, card, approach, prep);
  return Boolean(tab);
}

// The PDF is built after the tab opens and stored in the tab's button as a data link, so the
// button downloads it directly and keeps working even if the Event Finder page is closed.
async function attachPdf(tab, card, approach, prep) {
  try {
    const doc = await buildFlowChartPdf(card, approach, prep);
    const href = doc.output("datauristring", { filename: flowPdfName(card) });
    let button = null;
    for (let tries = 0; tries < 50 && !button && !tab.closed; tries += 1) {
      button = tab.document.getElementById("pdf");
      if (!button) await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (!button) return;
    button.href = href;
    button.download = flowPdfName(card);
    button.textContent = "\u2B07 Save as PDF";
    button.classList.remove("wait");
  } catch (err) {
    if (!tab.closed) tab.document.getElementById("pdf")?.replaceChildren("PDF unavailable");
  }
}
