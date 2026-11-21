"use strict";
/* ---------- utilità ---------- */
function el(tag, props, ...kids) {
  const e = document.createElement(tag);
  if (props) for (const [k, v] of Object.entries(props)) {
    if (v === false || v == null) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
const NS = "http://www.w3.org/2000/svg";
function sv(tag, attrs, ...kids) {
  const e = document.createElementNS(NS, tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) if (v != null) e.setAttribute(k, v);
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const NOTA_DISS = "* Quota delle corse con ritardi di almeno 15’ o soppressioni parziali o totali";
const FONTE = "elaborazione su dati ViaggiaTreno (portale Trenitalia)";
const FONT = '"Rethink Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const nf1 = new Intl.NumberFormat("it-IT", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("it-IT", { maximumFractionDigits: 0 });
const f1 = v => v == null ? "n.d." : nf1.format(v);
const f0 = v => v == null ? "n.d." : nf0.format(v);
const fp = v => v == null ? "n.d." : nf1.format(v) + "%";
const pl = (n, sing, plur) => n === 1 ? sing : plur;
const orem = m => { m = Math.round(m || 0); const h = Math.floor(m / 60), r = m % 60;
  if (!h) return r + " min"; return h + (h === 1 ? " ora" : " ore") + (r ? " " + r + " min" : ""); };
function spezza(t, max) {
  const out = []; let cur = "";
  for (const p of String(t).split(" ")) { if (cur && (cur + " " + p).length > max) { out.push(cur); cur = p; } else cur = cur ? cur + " " + p : p; }
  if (cur) out.push(cur);
  return out.length > 1 ? out : t;
}
const cap = s => s.charAt(0).toUpperCase() + s.slice(1);
const dm = iso => iso.slice(8, 10) + "/" + iso.slice(5, 7);
const dmy = iso => iso.slice(8, 10) + "/" + iso.slice(5, 7) + "/" + iso.slice(0, 4);
const dataLunga = iso => cap(new Date(iso + "T12:00:00").toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long", year: "numeric" }));
const nomeStaz = s => (s || "?").toLowerCase().replace(/(^|[\s'’.\-])(\p{L})/gu, (m, a, b) => a + b.toUpperCase()).replace("Ponte S.Pietro", "Ponte San Pietro");
const DIREZ = { partenza: "Partenza", arrivo: "Arrivo", transito: "Transito", tutte: "Tutte" };
const tratta = r => nomeStaz(r.da || (r.direzione === "partenza" ? "" : r.controparte)) + " → " + nomeStaz(r.a || (r.direzione === "partenza" ? r.controparte : ""));
const NOMI_CORTI = { "Lunedì": "Lun", "Martedì": "Mar", "Mercoledì": "Mer", "Giovedì": "Gio", "Venerdì": "Ven", "Sabato": "Sab", "Domenica": "Dom" };
const piccolo = () => window.innerWidth < 600;
const quando = iso => new Date(iso).toLocaleString("it-IT", { dateStyle: "long", timeStyle: "short" });

/* ---------- etichetta per la mappa di calore ---------- */
const tip = el("div", { class: "tip", role: "tooltip" });
document.body.append(tip);
function showTip(x, y, titolo, righe) {
  tip.replaceChildren(el("div", { class: "tip-t", text: titolo }),
    ...righe.map(r => el("div", { class: "tip-r" }, el("strong", { text: r.valore }), el("span", { class: "nome", text: r.nome }))));
  tip.style.display = "block";
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let px = x + 14, py = y + 14;
  if (px + w > innerWidth - 8) px = x - w - 14;
  if (py + h > innerHeight - 8) py = y - h - 14;
  tip.style.left = Math.max(8, px) + "px"; tip.style.top = Math.max(8, py) + "px";
}
function hideTip() { tip.style.display = "none"; }

/* ---------- grafici (Chart.js) ---------- */
const charts = new Map();
const crosshair = {
  id: "crosshair",
  afterDatasetsDraw(chart, args, opts) {
    if (!opts || !opts.on) return;
    const a = chart.tooltip && chart.tooltip.getActiveElements ? chart.tooltip.getActiveElements() : [];
    if (!a.length) return;
    const x = a[0].element.x, ca = chart.chartArea, ctx = chart.ctx;
    ctx.save(); ctx.strokeStyle = css("--muted"); ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, ca.top); ctx.lineTo(x, ca.bottom); ctx.stroke(); ctx.restore();
  }
};
Chart.register(crosshair);
Chart.defaults.font.family = FONT;

function tooltipBase(extra) {
  return Object.assign({
    backgroundColor: css("--card"), titleColor: css("--ink"), bodyColor: css("--ink2"),
    borderColor: css("--border"), borderWidth: 1, padding: 10, boxPadding: 4, usePointStyle: true
  }, extra || {});
}
function scalaValori(extra) {
  const s = {
    beginAtZero: true, suggestedMax: 5,
    grid: { color: css("--grid"), lineWidth: 1, borderDash: [1, 4], drawTicks: false }, border: { display: false },
    ticks: { color: css("--muted"), padding: 8, callback: v => nf0.format(v) + "%" }
  };
  return Object.assign(s, extra || {});
}
function scalaCategorie(extra) {
  return Object.assign({
    grid: { display: false }, border: { color: css("--grid") },
    ticks: { color: css("--muted"), maxRotation: 0, autoSkip: false, font: { size: piccolo() ? 11 : 12 } }
  }, extra || {});
}
function disegnaGrafico(id, plot, cfg, aria) {
  const old = charts.get(id); if (old) old.destroy();
  plot.replaceChildren();
  const cv = el("canvas", { role: "img", "aria-label": aria || "" });
  plot.append(cv);
  charts.set(id, new Chart(cv, cfg));
}
const messaggio = (plot, testo) => plot.replaceChildren(el("div", { class: "msg", text: testo }));

const LEG = [
  { nome: "Ritardi di almeno 5’", campo: "pct_ritardi_5", n: "n_ritardi_5", base: "effettuate", css: "--c1" },
  { nome: "Ritardi di almeno 15’", campo: "pct_ritardi_15", n: "n_ritardi_15", base: "effettuate", css: "--c2" },
  { nome: "Soppressioni totali", campo: "pct_soppresse", n: "soppresse", base: "programmate", css: "--c3" },
  { nome: "Soppressioni parziali", campo: "pct_sopp_parziali", n: "sopp_parziali", base: "programmate", css: "--c4" }
];
const colori = () => LEG.map(l => css(l.css));
const testoValore = (s, l) => fp(s[l.campo]) + (s[l.base] ? " (" + s[l.n] + "/" + s[l.base] + ")" : "");

function lineaAndamento(plot, gg) {
  if (gg.length < 2) { messaggio(plot, "Servono almeno due giorni di dati per disegnare l’andamento."); return; }
  const cols = colori(), cardC = css("--card");
  const ds = LEG.map((l, i) => ({
    label: l.nome, data: gg.map(g => g[l.campo]), borderColor: cols[i], backgroundColor: cols[i], borderWidth: 2, tension: 0, spanGaps: false,
    pointRadius: gg.length <= 45 ? 4 : 0, pointHoverRadius: 6, pointHitRadius: 14, pointBorderWidth: 2,
    pointBackgroundColor: gg.map(g => g.parziale ? cardC : cols[i]), pointBorderColor: cols[i]
  }));
  disegnaGrafico("andamento", plot, {
    type: "line", data: { labels: gg.map(g => dm(g.data)), datasets: ds },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: { right: 8, top: 6 } },
      interaction: { mode: "index", axis: "x", intersect: false },
      plugins: {
        legend: { display: false }, crosshair: { on: true },
        tooltip: tooltipBase({
          callbacks: {
            title: items => items.length ? dataLunga(gg[items[0].dataIndex].data) + (gg[items[0].dataIndex].senza_dati ? " (nessun dato)" : gg[items[0].dataIndex].parziale ? " (in corso)" : "") : "",
            label: it => " " + it.dataset.label + ": " + testoValore(gg[it.dataIndex], LEG[it.datasetIndex])
          }
        })
      },
      scales: { x: scalaCategorie({ ticks: { color: css("--muted"), maxRotation: 0, autoSkip: true, maxTicksLimit: piccolo() ? 5 : 10 } }), y: scalaValori() }
    }
  }, "Andamento giornaliero di ritardi e soppressioni");
}

function barreVerticali(plot, cats, righe, extra) {
  const cols = colori();
  const ds = LEG.map((l, i) => ({
    label: l.nome, data: righe.map(r => r[l.campo]), backgroundColor: cols[i], borderColor: cols[i], borderWidth: 0,
    borderRadius: 3, borderSkipped: "bottom", maxBarThickness: 22
  }));
  disegnaGrafico("giorni", plot, {
    type: "bar", data: { labels: cats, datasets: ds },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: { right: 8, top: 6 } },
      interaction: { mode: "index", intersect: false },
      datasets: { bar: { categoryPercentage: 0.8, barPercentage: 0.9 } },
      plugins: {
        legend: { display: false },
        tooltip: tooltipBase({
          callbacks: {
            title: items => items.length ? righe[items[0].dataIndex].nome : "",
            label: it => " " + it.dataset.label + ": " + fp(it.parsed.y),
            afterBody: items => items.length ? [extra(righe[items[0].dataIndex])] : []
          }
        })
      },
      scales: { x: scalaCategorie(), y: scalaValori() }
    }
  }, "Disservizi per giorno della settimana");
}

function barreOrizzontali(plot, righe) {
  const cols = colori();
  plot.style.height = Math.max(180, righe.length * 80 + 54) + "px";
  const ds = LEG.map((l, i) => ({
    label: l.nome, data: righe.map(r => r[l.campo]), backgroundColor: cols[i], borderColor: cols[i], borderWidth: 0,
    borderRadius: 4, borderSkipped: "start", maxBarThickness: 14
  }));
  disegnaGrafico("direttrici", plot, {
    type: "bar", data: { labels: righe.map(r => piccolo() ? spezza(r.direttrice, 16) : r.direttrice), datasets: ds },
    options: {
      indexAxis: "y", responsive: true, maintainAspectRatio: false, animation: false, layout: { padding: { right: 8, top: 4 } },
      interaction: { mode: "index", axis: "y", intersect: false },
      datasets: { bar: { categoryPercentage: 0.8, barPercentage: 0.9 } },
      plugins: {
        legend: { display: false },
        tooltip: tooltipBase({
          callbacks: {
            title: items => items.length ? righe[items[0].dataIndex].direttrice : "",
            label: it => " " + it.dataset.label + ": " + fp(it.parsed.x),
            afterBody: items => items.length ? [f0(righe[items[0].dataIndex].programmate) + " " + pl(righe[items[0].dataIndex].programmate, "corsa programmata", "corse programmate")] : []
          }
        })
      },
      scales: {
        x: scalaValori({ position: "top" }),
        y: { grid: { display: false }, border: { color: css("--grid") }, ticks: { color: css("--ink2"), autoSkip: false, font: { size: piccolo() ? 11 : 12.5 } } }
      }
    }
  }, "Disservizi per direttrice");
}

/* ---------- mappa di calore giorno e ora ---------- */
function mappaCalore(plot, o) {
  plot.replaceChildren();
  const ore = o.ore, W = Math.max(300, plot.clientWidth || 640), L = 38, R = 6, T = 6, B = 26, rh = 30, H = T + B + rh * 7;
  const cw = (W - L - R) / ore.length;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": o.aria || "", style: "display:block;width:100%;height:auto" });
  const vmax = Math.max(10, ...o.celle.filter(x => x.effettuate >= o.minimo).map(x => x.pct_ritardi_5 || 0));
  const mappa = new Map(o.celle.map(x => [x.dow + "-" + x.ora, x]));
  o.giorni.forEach((nome, i) => svg.append(sv("text", { x: L - 6, y: T + i * rh + rh / 2 + 4, "text-anchor": "end", class: "cat" }, NOMI_CORTI[nome] || nome)));
  ore.forEach((h, j) => { if (cw >= 26 || j % 2 === 0) svg.append(sv("text", { x: L + j * cw + cw / 2, y: H - B + 16, "text-anchor": "middle" }, String(h))); });
  o.giorni.forEach((nome, i) => ore.forEach((h, j) => {
    const cella = mappa.get(i + "-" + h), x = L + j * cw, y = T + i * rh;
    const ok = cella && cella.effettuate >= o.minimo;
    const v = ok ? (cella.pct_ritardi_5 || 0) : null;
    const r = sv("rect", {
      x: x + 1, y: y + 1, width: Math.max(1, cw - 2), height: rh - 2, rx: 4,
      style: ok ? `fill:var(--c1);fill-opacity:${(0.07 + 0.93 * Math.min(1, v / vmax)).toFixed(3)}`
        : (cella ? "fill:none;stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3" : "fill:var(--grid);fill-opacity:.4")
    });
    if (cella) {
      const mostra = ev => showTip(ev.clientX, ev.clientY, `${nome}, ${String(h).padStart(2, "0")}:00-${String(h).padStart(2, "0")}:59`, [
        { valore: cella.pct_ritardi_5 == null ? "n.d." : f1(cella.pct_ritardi_5) + "%", nome: "ritardi di almeno 5’" },
        { valore: f1(cella.ritardo_medio), nome: "minuti di ritardo medio" },
        { valore: String(cella.effettuate), nome: "corse osservate" + (ok ? "" : " (poche: dato indicativo)") }]);
      r.addEventListener("pointermove", mostra); r.addEventListener("pointerleave", hideTip);
    }
    svg.append(r);
    if (ok && cw >= 30 && v >= 0) svg.append(sv("text", { x: x + cw / 2, y: y + rh / 2 + 4, "text-anchor": "middle", style: "pointer-events:none;font-size:11px;font-weight:600;fill:" + (v / vmax > 0.6 ? "#ffffff" : "var(--ink)") }, String(Math.round(v))));
  }));
  plot.append(svg);
}

/* ---------- scheda: titolo, controlli, legenda, grafico, tabella, fonte ---------- */
function tabellaEl(t) {
  return el("table", t.sx ? { class: "sx" } : null,
    el("thead", null, el("tr", null, t.head.map((h, i) => el("th", { text: h })))),
    el("tbody", null, t.rows.map(r => el("tr", null, r.map(v => el("td", { text: v }))))));
}
function scheda(box, o) {
  const plot = el("div", { class: "plot" + (o.auto ? " auto" : "") });
  const wrap = el("div", { class: "tablewrap", hidden: true });
  const leg = o.legenda ? el("div", { class: "legend" }, LEG.map(l => el("span", null, el("span", { class: "sw", style: "background:var(" + l.css + ")" }), l.nome))) : null;
  let tabellaVisibile = false;
  const btn = el("button", { type: "button", class: "tbl-btn", "aria-expanded": "false", text: "Mostra come tabella" });
  btn.addEventListener("click", () => {
    tabellaVisibile = !tabellaVisibile;
    plot.hidden = tabellaVisibile; wrap.hidden = !tabellaVisibile; if (leg) leg.hidden = tabellaVisibile;
    btn.setAttribute("aria-expanded", String(tabellaVisibile));
    btn.textContent = tabellaVisibile ? "Mostra come grafico" : "Mostra come tabella";
    if (!tabellaVisibile) o.disegna(plot);
  });
  const card = el("article", { class: "card", id: o.id },
    el("h3", { text: o.titolo }), o.desc ? el("p", { class: "desc", text: o.desc }) : null,
    o.controlli || null, leg, plot, wrap, btn,
    el("p", { class: "fonte" }, el("strong", { text: "Fonte: " }), FONTE));
  box.append(card);
  const rifai = () => { if (!tabellaVisibile) o.disegna(plot); wrap.replaceChildren(tabellaEl(o.tabella())); };
  rifai();
  if (o.suResize && "ResizeObserver" in window) {
    let w = plot.clientWidth;
    new ResizeObserver(() => { if (plot.clientWidth !== w && !tabellaVisibile) { w = plot.clientWidth; o.disegna(plot); } }).observe(plot);
  }
  return { rifai };
}

/* ---------- stato dei treni di oggi ---------- */
function statoTreno(r) {
  const s = statoBase(r);
  if (r.stimato && r.stato === "effettuato" && !(r.parziale)) s.testo += " (stima)";
  return s;
}
function statoBase(r) {
  if (r.stato === "soppresso") return { testo: "Soppresso", colore: "var(--c3)", ico: "✕" };
  if (r.stato === "nd") return { testo: "Dato non disponibile", colore: "var(--muted)", ico: "?" };
  if (r.parziale && (r.stato === "parziale" || r.stato === "effettuato")) return { testo: "Soppressione parziale", colore: "var(--c4)", ico: "◐" };
  if (r.stato === "effettuato") {
    const x = r.ritardo == null ? 0 : r.ritardo;
    if (x >= 15) return { testo: `Ritardo grave: ${x}’`, colore: "var(--c2)", ico: "!!" };
    if (x >= 5) return { testo: `Ritardo: ${x}’`, colore: "var(--c1)", ico: "!" };
    return { testo: x > 0 ? `Puntuale (+${x}’)` : "Puntuale", colore: "var(--ok)", ico: "✓" };
  }
  if (r.stato === "in_corso") return { testo: "In corso o atteso", colore: "var(--muted)", ico: "" };
  return { testo: "Programmato", colore: "var(--muted)", ico: "" };
}
const coppie = (a, b) => el("div", { class: "coppia" }, a, b);
const chipStato = s => el("span", { class: "stato" }, el("i", { style: "background:" + s.colore }), el("span", { class: "ico", text: s.ico }), s.testo);
const tile = (l, big, d, cls) => el("div", { class: "kpi" + (cls ? " " + cls : "") }, el("div", { class: "label", text: l }), el("div", { class: "value", text: big }), el("div", { class: "note", text: d || " " }));

/* ---------- sezioni ---------- */
let D = null, periodo = "tutto", tipoGiorni = "tutti";
const PERIODI = [["tutto", "Da inizio rilevazione"], ["30", "Ultimi 30 giorni"], ["7", "Ultimi 7 giorni"]];

let giornoOggi = true;
function disegnaOggi() {
  const box = document.getElementById("oggi-box"); box.replaceChildren();
  const haIeri = (D.ieri_elenco || []).length > 0;
  const righe = (giornoOggi ? D.oggi_elenco : D.ieri_elenco) || [];
  const giorno = giornoOggi ? D.oggi : D.ieri;
  const sub = document.getElementById("oggi-sub");
  sub.replaceChildren(el("strong", { text: dataLunga(giorno) + (righe.length ? "" : ": nessun treno registrato.") }));
  const btn = el("button", { type: "button", class: "tbl-btn ieri-btn", text: giornoOggi ? "Il riassunto di ieri" : "Torna a oggi" });
  btn.addEventListener("click", () => { giornoOggi = !giornoOggi; disegnaOggi(); });
  if (giornoOggi && !haIeri) btn.disabled = true, btn.title = "Non ci sono ancora dati di ieri";
  sub.append(" ", btn);
  if (!righe.length) return;
  const eff = righe.filter(r => r.stato === "effettuato"), rit5 = eff.filter(r => (r.ritardo || 0) >= 5).length,
    rit15 = eff.filter(r => (r.ritardo || 0) >= 15).length, sop = righe.filter(r => r.stato === "soppresso").length,
    par = righe.filter(r => r.parziale && (r.stato === "effettuato" || r.stato === "parziale")).length,
    parFuori = righe.filter(r => r.stato === "parziale").length;
  const attesi = righe.filter(r => r.stato === "programmato" || r.stato === "in_corso").length;
  const prog = eff.length + sop + parFuori, rilevati = prog;
  const diss = righe.filter(r => r.stato === "soppresso" || (r.parziale && (r.stato === "effettuato" || r.stato === "parziale")) || (r.stato === "effettuato" && (r.ritardo || 0) >= 15)).length;
  const sulleEffettuate = n => eff.length ? fp(100 * n / eff.length) + " delle corse effettuate" : "";
  const suCorse = n => prog ? `su ${f0(prog)} ${pl(prog, "corsa", "corse")} (${fp(100 * n / prog)})` : "";
  box.append(el("section", { class: "kpis" },
    coppie(tile("Corse monitorate", f0(rilevati), attesi ? `${f0(attesi)} ${pl(attesi, "ancora attesa", "ancora attese")}` : ""),
      tile("Incidenza dei disservizi*", rilevati ? fp(100 * diss / rilevati) : "n.d.", `${f0(diss)} su ${f0(rilevati)} ${pl(rilevati, "corsa", "corse")}`, "diss")),
    coppie(tile("Con ritardo di almeno 5’", f0(rit5), sulleEffettuate(rit5), "c1"),
      tile("Con ritardo di almeno 15’", f0(rit15), sulleEffettuate(rit15), "c2")),
    coppie(tile("Soppressioni totali", f0(sop), suCorse(sop), "c3"),
      tile("Soppressioni parziali", f0(par), suCorse(par), "c4"))));
  box.append(el("p", { class: "fonte" }, NOTA_DISS));
  box.append(el("p", { class: "fonte nota-staz" }, el("em", { text: "Sono riportate le stazioni di Bergamo, Treviglio e Ponte San Pietro perché da esse transitano tutti i treni che interessano la provincia di Bergamo" })));
  let soloAnomalie = false, dirTab = "tutti", stazSel = "tutte";
  const wrap = el("div", { class: "sc" });
  const cb = el("input", { type: "checkbox", id: "solo-anom" });
  cb.addEventListener("change", e => { soloAnomalie = e.target.checked; ridisegna(); });
  function ridisegna() {
    const rs = righe.filter(r => (dirTab === "tutti" || r.direzione === dirTab)
      && (stazSel === "tutte" || (r.stazioni || []).includes(stazSel))
      && (!soloAnomalie || r.stato === "soppresso" || r.parziale || (r.ritardo || 0) >= 5));
    wrap.replaceChildren(el("table", { class: "sx" },
      el("thead", null, el("tr", null, ["Orario", "Effettivo", "Stazione", "Direzione", "N. treno", "Tratta", "Stato"].map(h => el("th", { text: h })))),
      el("tbody", null, rs.map(r => el("tr", null,
        el("td", { text: r.orario }), el("td", { text: r.reale || "-" }), el("td", { text: r.stazione || "Bergamo" }), el("td", { text: DIREZ[r.direzione] }),
        el("td", { text: r.numero }),
        el("td", { text: tratta(r) }),
        el("td", null, chipStato(statoTreno(r))))))));
  }
  const seg = el("div", { class: "seg", role: "group", "aria-label": "Arrivi e partenze" });
  [["tutti", "Tutti"], ["arrivo", "Arrivi"], ["partenza", "Partenze"], ["transito", "In transito"]].forEach(([k, t]) => {
    const b = el("button", { type: "button", "aria-pressed": String(k === dirTab), text: t });
    b.addEventListener("click", () => { dirTab = k; [...seg.children].forEach(x => x.setAttribute("aria-pressed", String(x === b))); ridisegna(); });
    seg.append(b);
  });
  const stazioni = D.stazioni || [];
  const selStaz = el("select", { id: "sel-staz", "aria-label": "Stazione" }, el("option", { value: "tutte", text: "Tutte le stazioni" }),
    ...stazioni.map(x => el("option", { value: x.codice, text: x.nome })));
  selStaz.addEventListener("change", e => { stazSel = e.target.value; ridisegna(); });
  box.append(el("div", { class: "ctrl" }, cb, el("label", { for: "solo-anom", text: "Mostra solo ritardi di almeno 5’ e soppressioni" }), stazioni.length > 1 ? selStaz : "", seg));
  ridisegna(); box.append(wrap);
  box.append(el("p", { class: "fonte" }, el("strong", { text: "Nota: " }), "è indicato come “puntuale” il treno con un ritardo inferiore ai 5’. Ogni treno compare una sola volta, anche se passa da più stazioni. La colonna “Stazione” indica quella a cui è riferito il dato. Con “In transito” sono indicati i treni che non hanno capolinea nella stazione indicata" + (righe.some(r => r.stimato && r.stato === "effettuato") ? ". “Stima”: ritardo stimato dall’ultimo rilevamento disponibile, perché ViaggiaTreno non ha registrato l’arrivo nella stazione" : "")));
  const acc = (D.ritardi_accumulati || {})[giorno];
  if (acc) {
    box.append(el("div", { class: "accumulo" },
      el("h4", { text: "Ritardi accumulati dai treni" + (giornoOggi ? " (giornata in corso, dato provvisorio)" : "") }),
      el("div", { class: "righe" },
        el("div", { class: "riga" }, el("span", { class: "t", text: "Solo ritardi" }), el("span", { class: "v", text: orem(acc.solo_ritardi) })),
        el("div", { class: "riga" }, el("span", { class: "t", text: "Ritardi + soppressioni" }), el("span", { class: "v", text: orem(acc.totale) }))),
      el("p", { class: "fonte" }, "Somma dei ritardi di almeno 2’ delle corse effettuate (gli anticipi non contano). Nella seconda riga si aggiunge, per ogni soppressione, l’attesa fino alla corsa successiva della stessa tratta (escluso l’ultimo treno della giornata). Dettagli nella Metodologia")));
  }
  box.append(el("p", { class: "fonte" }, el("strong", { text: "Fonte: " }), FONTE));
}

function disegnaFiltri() {
  const f = document.getElementById("filtri"); f.replaceChildren();
  PERIODI.forEach(([k, t]) => f.append(el("button", { type: "button", "aria-pressed": String(periodo === k), text: t, onclick: () => { periodo = k; disegnaPeriodo(); } })));
  const vecchio = document.getElementById("filtri-giorni"); if (vecchio) vecchio.remove();
  if (D.giorni_con_dati >= 14) {
    const g = el("div", { class: "seg", id: "filtri-giorni", role: "group", "aria-label": "Giorni", style: "margin-left:12px" });
    [["tutti", "Tutti i giorni"], ["lav", "Lun-ven"], ["we", "Sab-dom"]].forEach(([k, t]) =>
      g.append(el("button", { type: "button", "aria-pressed": String(tipoGiorni === k), text: t, onclick: () => { tipoGiorni = k; disegnaPeriodo(); } })));
    f.parentNode.append(g);
  }
  const dp = document.getElementById("dati-periodo"); dp.replaceChildren();
  if (D.prima_data) {
    const n = D.giorni_con_dati;
    dp.append(el("strong", { text: `Dati dal ${dmy(D.prima_data)} al ${dmy(D.ultima_data)} (${n} ${n === 1 ? "giorno" : "giorni"})` }));
  }
}

function disegnaPeriodo() {
  disegnaFiltri();
  const P = D.periodi[periodo + (D.giorni_con_dati >= 14 && tipoGiorni === "lav" ? "_lav" : D.giorni_con_dati >= 14 && tipoGiorni === "we" ? "_we" : "")], T = P.totale;
  const avviso = document.getElementById("avviso"); avviso.replaceChildren();
  const kpi = document.getElementById("kpi"); kpi.replaceChildren();
  const ids = ["c-andamento", "c-giorni", "c-direttrici", "c-orari", "c-treni"].map(i => document.getElementById(i));
  ids.forEach(e => e.replaceChildren());
  for (const [id, c] of charts) { c.destroy(); charts.delete(id); }
  if (!T.programmate) {
    avviso.append(el("div", { class: "alert", text: "La raccolta è appena partita: i primi indicatori compariranno quando ci saranno treni con ritardo definitivo registrato." }));
    ids.forEach(e => e.append(el("p", { class: "lead", text: "Disponibile non appena la raccolta avrà registrato i primi treni." })));
    return;
  }
  if (D.giorni_con_dati < 14)
    avviso.append(el("div", { class: "alert", text: `Dati raccolti da ${D.giorni_con_dati} giorn${D.giorni_con_dati === 1 ? "o" : "i"}: i confronti per giorno della settimana, direttrice e singolo treno diventano significativi dopo alcune settimane di osservazione. Passando il cursore sui grafici compare un’etichetta con il numero di corse su cui si basa ogni valore; le tabelle riportano gli stessi dati.` }));
  kpi.append(coppie(
    tile("Corse analizzate", f0(T.programmate), `${f0(T.effettuate)} ${pl(T.effettuate, "effettuata", "effettuate")}, ${f0(T.soppresse)} ${pl(T.soppresse, "soppressa", "soppresse")} totalmente, ${f0(T.parziali_senza_passaggio)} ${pl(T.parziali_senza_passaggio, "soppressa", "soppresse")} parzialmente`),
    tile("Incidenza dei disservizi*", fp(T.pct_disservizi), `${f0(T.disservizi)} su ${f0(T.programmate)} ${pl(T.programmate, "corsa", "corse")}`, "diss")),
    coppie(tile("Ritardi di almeno 5’", fp(T.pct_ritardi_5), `${f0(T.n_ritardi_5)} su ${f0(T.effettuate)} ${pl(T.effettuate, "corsa effettuata", "corse effettuate")}`, "c1"),
    tile("Ritardi di almeno 15’", fp(T.pct_ritardi_15), `${f0(T.n_ritardi_15)} su ${f0(T.effettuate)} ${pl(T.effettuate, "corsa effettuata", "corse effettuate")}`, "c2")),
    coppie(tile("Soppressioni totali", fp(T.pct_soppresse), `${f0(T.soppresse)} su ${f0(T.programmate)} ${pl(T.programmate, "corsa programmata", "corse programmate")}`, "c3"),
    tile("Soppressioni parziali", fp(T.pct_sopp_parziali), `${f0(T.sopp_parziali)} su ${f0(T.programmate)} ${pl(T.programmate, "corsa programmata", "corse programmate")}`, "c4")));
  kpi.append(el("p", { class: "fonte" }, NOTA_DISS,
    T.stimate ? [el("br"), el("em", { text: `Di cui con ritardo stimato dall’ultimo rilevamento: ${f0(T.stimate)} (vedi Metodologia)` })] : null,
    T.nd ? [el("br"), `Corse non classificabili (dato non disponibile): ${f0(T.nd)}. Sono escluse dai calcoli`] : null));

  /* andamento giornaliero */
  const gg = P.giorni;
  scheda(ids[0], {
    id: "g-andamento", titolo: "Percentuale di corse in ritardo e soppresse, per giorno", legenda: true,
    desc: "Ogni punto è un giorno. Il punto vuoto è il giorno in corso, ancora incompleto." + (gg.some(g => g.senza_dati) ? " Gli spazi senza linea sono giorni senza dati (raccolta interrotta)." : "") + " I ritardi sono calcolati sul totale delle corse effettuate. Le soppressioni, totali e parziali, sono calcolate sul totale delle corse programmate.",
    disegna: plot => lineaAndamento(plot, gg),
    tabella: () => ({
      head: ["Giorno", "Corse programmate", "Effettuate", "Soppresse", "Soppresse in parte", "Ritardi di almeno 5’", "% almeno 5’", "Ritardi di almeno 15’", "% almeno 15’", "Ritardo medio (’)"],
      rows: gg.map(g => g.senza_dati ? [dm(g.data) + " (nessun dato)", "-", "-", "-", "-", "-", "-", "-", "-", "-"] : [dm(g.data) + (g.parziale ? " (in corso)" : ""), f0(g.programmate), f0(g.effettuate), f0(g.soppresse), f0(g.sopp_parziali), f0(g.n_ritardi_5), fp(g.pct_ritardi_5), f0(g.n_ritardi_15), fp(g.pct_ritardi_15), f1(g.ritardo_medio)])
    })
  });

  /* giorni della settimana */
  const dw = P.giorno_settimana;
  scheda(ids[1], {
    id: "g-giorni", titolo: "Disservizi per giorno della settimana", legenda: true,
    desc: "Sono riepilogati solo i giorni completi (il giorno in corso viene escluso). Il numero di giorni osservati compare nell’etichetta.",
    disegna: plot => barreVerticali(plot, dw.map(d => piccolo() ? NOMI_CORTI[d.nome] : d.nome), dw, d => `${d.giorni_osservati} ${pl(d.giorni_osservati, "giorno osservato", "giorni osservati")}, ${f0(d.programmate)} ${pl(d.programmate, "corsa", "corse")}`),
    tabella: () => ({
      head: ["Giorno", "Giorni osservati", "Corse programmate", "% ritardi di almeno 5’", "% ritardi di almeno 15’", "% soppresse", "% soppresse in parte", "Ritardo medio (’)"],
      rows: dw.map(d => [d.nome, f0(d.giorni_osservati), f0(d.programmate), fp(d.pct_ritardi_5), fp(d.pct_ritardi_15), fp(d.pct_soppresse), fp(d.pct_sopp_parziali), f1(d.ritardo_medio)])
    })
  });

  /* pulsanti di periodo, propri di direttrici e fasce orarie */
  const suff = D.giorni_con_dati >= 14 && tipoGiorni === "lav" ? "_lav" : D.giorni_con_dati >= 14 && tipoGiorni === "we" ? "_we" : "";
  const blocco = k => D.periodi[k + suff] || D.periodi[k];
  const pulsantiPeriodo = (iniziale, alCambio) => {
    const g = el("div", { class: "seg", role: "group", "aria-label": "Periodo" });
    PERIODI.forEach(([k, t]) => {
      const b = el("button", { type: "button", "aria-pressed": String(k === iniziale), text: t });
      b.addEventListener("click", () => { [...g.children].forEach(x => x.setAttribute("aria-pressed", String(x === b))); alCambio(k); });
      g.append(b);
    });
    return el("div", { class: "controls" }, g);
  };

  /* direttrici */
  let perDir = periodo;
  const dirDelPeriodo = () => blocco(perDir).direttrici.filter(d => d.direzione === "tutte" && d.programmate > 0).sort((a, b) => b.programmate - a.programmate);
  let scDir;
  scDir = scheda(ids[2], {
    id: "g-direttrici", titolo: "Disservizi per direttrice", legenda: true, controlli: pulsantiPeriodo(perDir, k => { perDir = k; scDir.rifai(); }),
    desc: "Tipologia e incidenza di disagi per ciascuna linea.",
    disegna: plot => { const r = dirDelPeriodo(); r.length ? barreOrizzontali(plot, r) : messaggio(plot, "Nessuna corsa per questo periodo."); },
    tabella: () => ({
      sx: false,
      head: ["Direttrice", "Corse programmate", "Effettuate", "Soppresse", "Soppresse in parte", "% ritardi di almeno 5’", "% ritardi di almeno 15’", "% soppresse", "% soppresse in parte", "Ritardo medio (’)"],
      rows: dirDelPeriodo().map(d => [d.direttrice, f0(d.programmate), f0(d.effettuate), f0(d.soppresse), f0(d.sopp_parziali), fp(d.pct_ritardi_5), fp(d.pct_ritardi_15), fp(d.pct_soppresse), fp(d.pct_sopp_parziali), f1(d.ritardo_medio)])
    })
  });

  /* fasce orarie: mappa di calore */
  let perOre = periodo;
  const nomiGiorni = P.giorno_settimana.map(d => d.nome);
  const datiOre = () => {
    const Q = blocco(perOre), ore = [...new Set(Q.fasce_orarie.map(f => f.ora))].sort((a, b) => a - b);
    return { hm: Q.heatmap, orePiene: ore.length ? Array.from({ length: ore[ore.length - 1] - ore[0] + 1 }, (_, i) => ore[0] + i) : [] };
  };
  let scOre;
  scOre = scheda(ids[3], {
    id: "g-orari", titolo: "Le fasce orarie più critiche", auto: true, suResize: true, controlli: pulsantiPeriodo(perOre, k => { perOre = k; scOre.rifai(); }),
    desc: "Per ciascuna fascia oraria è indicata la percentuale delle corse con ritardi di almeno 5’ sul totale delle corse effettuate. L’intensità del colore corrisponde alla frequenza dei disagi. I riquadri tratteggiati indicano fasce orarie con meno di 3 corse, quindi meno attendibili.",
    disegna: plot => { const { hm, orePiene } = datiOre(); orePiene.length ? mappaCalore(plot, { aria: "Mappa di calore dei ritardi per giorno e ora", ore: orePiene, giorni: nomiGiorni, celle: hm, minimo: 3 }) : messaggio(plot, "Dati non ancora sufficienti."); },
    tabella: () => {
      const { hm, orePiene } = datiOre();
      return {
        head: ["Giorno", ...orePiene.map(h => String(h))],
        rows: nomiGiorni.map((n, i) => [n, ...orePiene.map(h => { const x = hm.find(z => z.dow === i && z.ora === h); return x ? (x.pct_ritardi_5 == null ? "-" : f0(x.pct_ritardi_5) + "%") + ` (${x.effettuate})` : ""; })])
      };
    }
  });

  disegnaTreni(ids[4], P.treni);
}

function disegnaTreni(box, treni) {
  let ordine = "programmate", verso = -1, minimo = 3, cerca = "", mostra = 25;
  const sec = el("article", { class: "card", id: "g-treni" });
  const corpo = el("div", { class: "tablewrap", style: "max-height:none" });
  const mpiu = el("button", { type: "button", class: "link" });
  const info = el("p", { class: "desc" });
  const campi = [
    ["numero", "N. treno", false, t => t.numero],
    ["controparte", "Tratta", false, t => tratta(t)],
    ["stazione", "Stazione", false, t => t.stazione || "Bergamo"],
    ["direttrice", "Direttrice", false, t => t.direttrice],
    ["orario", "Orario", false, t => t.orario], ["programmate", "Corse", true, t => f0(t.programmate)], ["ritardo_medio", "Ritardo medio (’)", true, t => f1(t.ritardo_medio)],
    ["pct_ritardi_5", "% ritardi di almeno 5’", true, t => fp(t.pct_ritardi_5)], ["n_ritardi_15", "Ritardi di almeno 15’", true, t => f0(t.n_ritardi_15)],
    ["pct_ritardi_15", "% ritardi di almeno 15’", true, t => fp(t.pct_ritardi_15)], ["soppresse", "Soppressioni totali", true, t => f0(t.soppresse)], ["pct_soppresse", "% soppresse", true, t => fp(t.pct_soppresse)],
    ["sopp_parziali", "Soppressioni parziali", true, t => f0(t.sopp_parziali)], ["pct_sopp_parziali", "% soppresse in parte", true, t => fp(t.pct_sopp_parziali)]];
  function ridisegna() {
    const q = cerca.trim().toUpperCase();
    const rs = treni.filter(t => t.programmate >= minimo && (!q || (t.numero + " " + (t.da || "") + " " + (t.a || "") + " " + (t.controparte || "") + " " + t.direttrice).toUpperCase().includes(q)));
    rs.sort((a, b) => {
      const x = a[ordine], y = b[ordine]; if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(y, "it", { numeric: true }) : x - y) * verso;
    });
    info.textContent = `${rs.length} ${pl(rs.length, "treno", "treni")}${rs.length !== treni.length ? ` (su ${treni.length})` : ""}. Clicca sulle intestazioni per ordinare.`;
    corpo.replaceChildren(el("table", null,
      el("thead", null, el("tr", null, campi.map(([k, t, num]) => {
        const th = el("th", { class: "ord", text: t + (ordine === k ? (verso > 0 ? " ▲" : " ▼") : ""), tabindex: "0", style: num ? "" : "text-align:left" });
        th.addEventListener("click", () => { if (ordine === k) verso = -verso; else { ordine = k; verso = num ? -1 : 1; } ridisegna(); });
        th.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); th.click(); } });
        return th;
      }))),
      el("tbody", null, rs.slice(0, mostra).map(t => el("tr", null, campi.map(([k, , num, f]) => el("td", { text: f(t), style: num ? "" : "text-align:left" })))))));
    mpiu.classList.toggle("hidden", rs.length <= mostra);
    mpiu.textContent = `Mostra altri 25 (${rs.length - mostra} nascosti)`;
  }
  mpiu.addEventListener("click", () => { mostra += 25; ridisegna(); });
  const ricerca = el("input", { type: "search", placeholder: "Cerca numero o tratta…", "aria-label": "Cerca treno" });
  ricerca.addEventListener("input", e => { cerca = e.target.value; mostra = 25; ridisegna(); });
  const sel = el("select", { "aria-label": "Corse minime" }, [1, 3, 5, 10, 20].map(n => el("option", { value: n, text: n + " corse", selected: n === minimo ? true : null })));
  sel.addEventListener("change", e => { minimo = +e.target.value; mostra = 25; ridisegna(); });
  sec.append(el("h3", { text: "Ritardi e soppressioni di ciascun treno" }),
    el("p", { class: "desc", text: "Ogni corsa è identificata da un numero: qui è possibile conoscere le performance di ciascun treno nel corso del tempo." }),
    el("div", { class: "controls" }, ricerca, el("label", null, "Almeno ", sel)), info, corpo, mpiu,
    el("p", { class: "fonte" }, el("strong", { text: "Nota: " }), "il ritardo medio considera i treni in anticipo come puntuali. A metà dicembre, con il cambio d’orario, la numerazione dei treni potrebbe variare"),
    el("p", { class: "fonte" }, el("strong", { text: "Fonte: " }), FONTE));
  box.append(sec);
  ridisegna();
}

function disegnaDati() {
  const box = document.getElementById("dati-box"); box.replaceChildren();
  const files = D.file_giornalieri || [];
  box.append(el("p", { class: "lead", text: "Tutti i dati raccolti sono liberamente scaricabili in formato .csv, per rielaborarli." }),
    el("ul", { class: "dl" },
      el("li", null, el("a", { href: "dati/corse_complete.csv", text: "corse_complete.csv" }), ": tutte le corse registrate, una riga per treno, stazione e direzione (un treno che passa da più stazioni compare in più righe; nel portale è contato una sola volta)."),
      el("li", null, el("a", { href: "dati/riepilogo.json", text: "riepilogo.json" }), ": gli indicatori calcolati, usati da questa pagina.")),
    el("details", null,
      el("summary", { text: files.length === 1 ? "File giornalieri (ultimo giorno)" : "File giornalieri (ultimi " + files.length + " giorni)" }),
      el("div", { class: "files" }, files.slice().reverse().map(d => el("div", null, el("a", { href: `dati/corse/${d}.csv`, text: d + ".csv" })))),
      el("p", { class: "fonte" }, el("strong", { text: "Colonne principali: " }), "data; stazione (codice della stazione monitorata a cui si riferisce la riga); direzione (partenza o arrivo dalla stazione); numero_treno; controparte (capolinea di origine o destinazione); origine e destinazione (capolinea della corsa, quando disponibili); orario_programmato e orario_reale; ritardo_min (negativo = anticipo); stato (effettuato, soppresso, parziale = soppressione parziale con la stazione non servita, nd = dato non disponibile); via (percorso dei treni da e per Milano); provvedimento e tipo_treno (codici grezzi ViaggiaTreno); parziale (1 = soppressione parziale); stimato (1 = ritardo stimato dall’ultimo rilevamento); fermate_soppresse (fermate non effettuate, separate da |); motivo_ritardo. Citare: “elaborazione di Luca Bonzanni su dati ViaggiaTreno”")),
    el("p", { class: "fonte" }, "Il codice che raccoglie ed elabora i dati è pubblico: ", el("a", { href: "https://github.com/lucabonzanni/monitor-treni-bergamo", text: "codice e dati su GitHub" })));
}

function disegnaMetodologia() {
  const nc = D.controparti_non_classificate || [];
  const voci = [
    ["Fonte", "I dati sono elaborati sulla base del sistema pubblico ViaggiaTreno (Rfi/Trenitalia), che raccoglie anche i treni regionali Trenord. Non è un’Api ufficialmente documentata (può cambiare o interrompersi senza preavviso)."],
    ["Stazioni monitorate", "Il portale segue i treni regionali che toccano le stazioni di " + ((D.stazioni || []).map(x => x.nome).join(", ") || "Bergamo") + ", per avere un quadro dei treni della Bergamasca. Sono esclusi Frecce, Intercity, Euronight e gli altri treni a lunga percorrenza. Ogni corsa è contata una sola volta, anche se passa da più stazioni monitorate o compare sia in arrivo sia in partenza: il dato è quello della stazione con priorità più alta (nell’ordine: Bergamo, Treviglio, Ponte San Pietro), salvo che in quella stazione non sia disponibile."],
    ["Cosa viene misurato", "Per ogni treno che parte, arriva o transita in una stazione monitorata, viene misurata la differenza tra l’orario programmato e quello reale registrato in quella stazione (per le partenze conta l’orario di partenza, per gli arrivi e i transiti l’orario di arrivo)."],
    ["Ritardo e puntualità", "Vengono segnalati i treni con almeno 5 minuti di ritardo e con almeno 15 minuti di ritardo. Un treno in anticipo viene considerato comunque come puntuale."],
    ["Soppressioni", "Sono indicate come soppressioni totali solo le corse interamente non eseguite. Sono soppressioni parziali le corse che partono da una stazione diversa o terminano in una stazione diversa dal previsto (per esempio un Bergamo-Milano Centrale che parte da Treviglio Ovest invece che da Bergamo, o che termina a Verdello Dalmine): sono comunque un disservizio per chi viaggia. Le soppressioni parziali sono conteggiate a parte. Se la corsa passa comunque dalla stazione monitorata, il suo ritardo entra nel calcolo; se la stazione non è servita, non c’è nessun orario da misurare. La classificazione è in fase di verifica sui casi reali: i codici grezzi sono nel file .csv."],
    ["Incidenza dei disservizi", "Somma le corse con ritardo di almeno 15 minuti, le soppressioni totali e le soppressioni parziali, rapportata alle corse monitorate. Ogni corsa è contata una sola volta, anche se ha più di un problema."],
    ["Ritardi accumulati", "Sotto il tabellone di giornata compare la somma dei minuti di ritardo accumulati dai treni (non i minuti persi dai singoli passeggeri). Nella riga “Solo ritardi” si sommano i ritardi delle corse effettuate: contano i ritardi di almeno 2 minuti, perché un solo minuto potrebbe essere un arrotondamento, e gli anticipi valgono zero, senza compensare i ritardi. Nella riga “Ritardi + soppressioni” si aggiunge, per ogni soppressione totale o parziale senza passaggio nella stazione, l’attesa tra l’orario programmato della corsa soppressa e il passaggio della corsa successiva della stessa tratta (stessa stazione, stessa direzione e stesso capolinea). L’ultima corsa della giornata di ogni tratta non ne ha una successiva e non è conteggiata. Per la giornata in corso il dato è provvisorio."],
    ["Giorni lavorativi e fine settimana", "Quando i dati coprono almeno due settimane, il quadro complessivo si può restringere ai giorni lavorativi (lunedì-venerdì) o al fine settimana (sabato e domenica). Le festività infrasettimanali sono contate in base al giorno della settimana."],
    ["Percentuali", "I ritardi sono calcolati sulle corse effettuate con dato rilevato; le soppressioni, totali e parziali, sulle corse programmate (tutte quelle previste, con dato rilevato). Questo vale sia per la sezione di oggi sia per il quadro complessivo. Le corse senza dato definitivo (nd) sono escluse dai calcoli e contate a parte: il loro numero compare sotto i numeri del quadro quando ce ne sono."],
    ["Copertura", "La rilevazione è iniziata " + (D.prima_data ? dataLunga(D.prima_data).toLowerCase() : "-") + (D.stazioni && D.stazioni.length > 1 ? " per Bergamo; " + D.stazioni.slice(1).map(x => x.nome + " è monitorata dal " + dataLunga(x.data_inizio).toLowerCase()).join(", ") + ": con l’ingresso di nuove stazioni, i dati complessivi cambiano definizione e i confronti con i giorni precedenti vanno fatti con cautela" : "") + " (non esiste uno storico precedente a tali date). Il servizio ferroviario tra Ponte San Pietro e Bergamo è sostituito da autobus: in questa fase, i treni da Lecco e da Milano via Carnate si fermano a Ponte San Pietro e compaiono solo nei dati di quella stazione. Il dato di ogni corsa si completa dopo il passaggio del treno; un treno può non comparire se ViaggiaTreno non lo mostra nelle tabelle della stazione. Se ViaggiaTreno perde un treno nell’ultima stazione prima di quella monitorata e la corsa, che ha la stazione come capolinea, non risulta soppressa, dopo 30 minuti dall’arrivo atteso il ritardo viene stimato dall’ultimo rilevamento disponibile (colonna stimato nel file .csv)."],
    ["Direttrici", "Per Bergamo-Milano si distingue tra via Pioltello (treni che fermano a Treviglio o Pioltello) e via Carnate (treni che fermano a Carnate o Paderno d’Adda), in base alle fermate intermedie registrate da ViaggiaTreno."],
    ["Aggiornamento", "La raccolta parte automaticamente circa ogni 15 minuti; gli avvii possono subire ritardi, quindi i dati più recenti possono arrivare con qualche minuto di scarto. Ultimo aggiornamento dei dati: " + (D.aggiornato_il ? quando(D.aggiornato_il) : "-") + "."],
    ["Interpretazione", "Le medie su pochi giorni risentono di singoli eventi (scioperi, guasti, lavori). Le tabelle indicano sempre il numero di corse su cui si basa ogni valore."]
  ];
  const det = el("details", { class: "tendina", id: "met-tendina" },
    el("summary", { text: "Leggi metodologia, note e avvertenze" }),
    el("div", { class: "met" }, ...voci.map(([k, t]) => el("p", null, el("b", { text: k + ". " }), t))));
  const apri = () => { if (location.hash === "#metodologia") det.open = true; };
  window.addEventListener("hashchange", apri); apri();
  document.getElementById("metodologia-box").replaceChildren(det);
}

function disegnaCondividi() {
  const u = encodeURIComponent(location.href.split("#")[0]), t = encodeURIComponent(document.title);
  const box = document.getElementById("share"); box.replaceChildren();
  const lnk = (href, testo) => el("a", { href, target: "_blank", rel: "noopener", text: testo });
  const copia = el("button", { type: "button", text: "Copia il link" });
  copia.addEventListener("click", () => {
    (navigator.clipboard ? navigator.clipboard.writeText(location.href.split("#")[0]) : Promise.reject())
      .then(() => { copia.textContent = "Link copiato"; }).catch(() => { copia.textContent = "Copia non riuscita"; });
  });
  box.append(el("span", { class: "tb-l", text: "Condividi il progetto:" }),
    lnk(`https://www.linkedin.com/sharing/share-offsite/?url=${u}`, "LinkedIn"),
    lnk(`https://www.facebook.com/sharer/sharer.php?u=${u}`, "Facebook"),
    lnk(`https://twitter.com/intent/tweet?url=${u}&text=${t}`, "X"),
    lnk(`https://wa.me/?text=${t}%20${u}`, "WhatsApp"),
    el("a", { href: `mailto:?subject=${t}&body=${u}`, text: "E-mail" }), copia);
}

function init(d) {
  D = d;
  document.getElementById("stamp").textContent = d.aggiornato_il ? "Ultimo aggiornamento dei dati: " + quando(d.aggiornato_il) : "In attesa dei primi dati.";
  disegnaCondividi(); disegnaOggi(); disegnaPeriodo(); disegnaDati(); disegnaMetodologia();
}
if (window.matchMedia) {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const cambio = () => { if (D) disegnaPeriodo(); };
  if (mq.addEventListener) mq.addEventListener("change", cambio); else if (mq.addListener) mq.addListener(cambio);
}
fetch("dati/riepilogo.json", { cache: "no-store" }).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); }).then(init).catch(() => {
  document.getElementById("stamp").textContent = "I dati non sono ancora disponibili: la prima raccolta non è ancora stata completata.";
  disegnaCondividi();
});
