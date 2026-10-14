// Plain English for the desk: METARs, TAFs, the FAA Command Center's advisories, NWS forecast discussions, the
// agent's changes and tool calls. Pure functions; times are minutes from local midnight of the task's date.
import { utcAt } from "./model.js";

const KT_MPH = 1.15078;
const COMPASS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split(" ");
const PHENOMENA = { DZ: "drizzle", RA: "rain", SN: "snow", SG: "snow grains", IC: "ice crystals", PL: "ice pellets",
  GR: "hail", GS: "small hail", UP: "precipitation", BR: "mist", FG: "fog", FU: "smoke", VA: "volcanic ash", DU: "dust",
  SA: "sand", HZ: "haze", PY: "spray", PO: "dust whirls", SQ: "squalls", FC: "funnel cloud", SS: "sandstorm", DS: "duststorm" };
const DRY = new Set(["BR", "FG", "FU", "VA", "DU", "SA", "HZ", "PY", "PO", "SQ", "FC", "SS", "DS"]);
const DESCRIBED = { MI: "shallow ", PR: "partial ", BC: "patches of ", DR: "drifting ", BL: "blowing ", FZ: "freezing " };
const SKY = { FEW: "Few clouds", SCT: "Partly cloudy", BKN: "Mostly cloudy", OVC: "Overcast", VV: "Overcast" };
const RANK = ["FEW", "SCT", "BKN", "OVC", "VV"];
const WX = /^(-|\+|VC)?((?:MI|PR|BC|DR|BL|SH|TS|FZ)*)((?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)*)$/;
const WIND = /^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?KT$/;
const VIS = /^(M|P)?(?:(\d+)\/(\d+)|(\d+))SM$/;
const CLOUD = /^(FEW|SCT|BKN|OVC|VV)(\d{3})/;
const TEMP = /^(M?\d{2})\/(M?\d{2})?$/;
const SKIP = /^(\d{6}Z|METAR|SPECI|AUTO|COR)$/;
const HUBS = { BOS: "Boston", JFK: "JFK" };
const CONDITIONS = { "LOW CEILINGS": "low cloud", "SNOW-ICE": "snow and ice", VOLUME: "heavy traffic", "RWY-TAXI": "runway work",
  CONSTRUCTION: "construction", OTHER: "", STAFFING: "staffing" };
const JARGON = [[/\bcigs\b/gi, "cloud"], [/-FZDZ\b/g, "freezing drizzle"], [/\bSCT to BKN\b/g, "scattered to broken"],
  [/\bTSRA\b/g, "thunderstorms"], [/\bSHRA\b/g, "showers"], [/\bTSTMS?\b/gi, "thunderstorms"], [/\bthru\b/gi, "through"],
  [/\bvsbys?\b/gi, "visibility"], [/\bterminals\b/g, "airports"]];

const pad = (n) => String(n).padStart(2, "0");
export const clock = (m) => {
  const r = ((Math.round(m) % 1440) + 1440) % 1440;
  return `${pad(Math.floor(r / 60))}:${pad(r % 60)}`;
};
const upper = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);
const and = (xs) => (xs.length > 1 ? `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}` : xs[0] ?? "");
const pyRound = (x) => {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 ? r - 1 : r;
};
const mph = (kt) => pyRound(kt * KT_MPH);

export function duration(min) {
  const m = Math.round(min), h = Math.floor(m / 60), r = m % 60;
  return h ? `${h} h${r ? ` ${r} min` : ""}` : `${r} min`;
}

/** "Light rain and mist · visibility 1 1/4 mi · cloud at 400 ft · wind NE 17 mph", word for word as the traffic
 * sidecar's (agentenv_airops.stage.metar), and the numbers behind it. */
export function metar(text) {
  const tokens = text.split(" RMK")[0].trim().split(/\s+/);
  const w = { wind_dir: null, wind_kt: 0, gust_kt: null, vis_sm: null, wx: [], clouds: [], ceiling_ft: null, temp_c: null, dew_c: null };
  let vis = null;
  tokens.forEach((tok, i) => {
    let m;
    if (i === 0 || SKIP.test(tok)) return;
    if ((m = WIND.exec(tok))) {
      w.wind_dir = m[1] === "VRB" ? null : +m[1];
      w.wind_kt = +m[2];
      w.gust_kt = m[3] ? +m[3] : null;
    } else if ((m = VIS.exec(tok))) {
      const whole = i > 1 && m[2] && /^\d+$/.test(tokens[i - 1]) ? +tokens[i - 1] : 0;
      const value = m[2] ? whole + m[2] / m[3] : +m[4];
      const said = (whole ? `${whole} ` : "") + (m[2] ? `${m[2]}/${m[3]}` : m[4]);
      w.vis_sm = Math.round(value * 100) / 100;
      vis = m[1] === "M" ? `under ${said}` : m[1] === "P" ? `over ${said}` : said;
    } else if ((m = CLOUD.exec(tok))) w.clouds.push([m[1], +m[2] * 100]);
    else if ((m = TEMP.exec(tok))) {
      w.temp_c = +m[1].replace("M", "-");
      w.dew_c = m[2] ? +m[2].replace("M", "-") : null;
    } else if (tok.length >= 2 && (m = WX.exec(tok)) && (m[2] || m[3])) w.wx.push(tok);
  });
  const ceil = w.clouds.filter(([c]) => c === "BKN" || c === "OVC" || c === "VV").map(([, ft]) => ft);
  w.ceiling_ft = ceil.length ? Math.min(...ceil) : null;
  const words = w.wx.map(phrase).join(" and ");
  const cover = w.clouds.reduce((best, [c]) => (best == null || RANK.indexOf(c) > RANK.indexOf(best) ? c : best), null);
  const parts = [words ? upper(words) : SKY[cover] ?? "Clear"];
  if (vis) parts.push(`visibility ${vis} mi`);
  if (w.ceiling_ft != null) parts.push(`cloud at ${w.ceiling_ft.toLocaleString("en-US")} ft`);
  if (w.wind_kt === 0) parts.push("calm");
  else parts.push(`wind ${w.wind_dir == null ? "variable" : COMPASS[pyRound(w.wind_dir / 22.5) % 16]} ${mph(w.wind_kt)} mph${w.gust_kt ? ` gusting ${mph(w.gust_kt)}` : ""}`);
  w.plain = parts.join(" · ");
  return w;
}

/** One weather group in words: -RA "light rain", +TSRA "thunderstorm with heavy rain", VCSH "showers nearby". */
export function phrase(code) {
  const [, sign = "", described = "", rest = ""] = WX.exec(code) ?? [];
  const found = rest.match(/../g) ?? [], marks = described.match(/../g) ?? [];
  let noun = found.map((p) => PHENOMENA[p]).join(" and ");
  const level = found.some((p) => !DRY.has(p)) ? { "-": "light ", "+": "heavy " }[sign] ?? "" : "";
  for (const d of marks) if (DESCRIBED[d] && noun) noun = DESCRIBED[d] + noun;
  if (marks.includes("SH")) noun = noun ? `${noun} showers` : "showers";
  const words = marks.includes("TS") ? (noun ? `thunderstorm with ${level}${noun}` : "thunderstorm") : `${level}${noun}`;
  return sign === "VC" ? `${words} nearby` : words;
}

/** The local minute of a UTC day of month and time, in the month of the task's date (or the next, past its end). */
export function zulu(task, day, hour, minute = 0) {
  const [y, mo, d] = task.date.split("-").map(Number);
  const month = day < d - 15 ? mo : mo - 1;
  return Math.round((Date.UTC(y, month, day, hour, minute) - utcAt(task, 0)) / 60e3);
}

function conditions(tokens) {
  const c = { wind_kt: null, wind_dir: null, gust_kt: null, vis_sm: null, wx: [], ceiling_ft: null, cb: false, shear: false, nsw: false };
  for (const tok of tokens) {
    let m;
    if ((m = WIND.exec(tok))) Object.assign(c, { wind_dir: m[1] === "VRB" ? null : +m[1], wind_kt: +m[2], gust_kt: m[3] ? +m[3] : null });
    else if ((m = VIS.exec(tok))) {
      c.vis_sm = m[2] ? m[2] / m[3] : +m[4] + (m[1] === "P" ? 0.5 : 0);
      c.vis = m[2] ? `${m[2]}/${m[3]}` : m[4];
    } else if (/^\d$/.test(tok) && VIS.test(tokens[tokens.indexOf(tok) + 1] ?? "")) continue;
    else if ((m = CLOUD.exec(tok))) {
      if ((m[1] === "BKN" || m[1] === "OVC" || m[1] === "VV") && c.ceiling_ft == null) c.ceiling_ft = +m[2] * 100;
      if (tok.endsWith("CB")) c.cb = true;
    } else if (/^WS\d{3}\//.test(tok)) c.shear = true;
    else if (tok === "NSW") c.nsw = true;
    else if (tok.length >= 2 && (m = WX.exec(tok)) && (m[2] || m[3])) c.wx.push(tok);
  }
  return c;
}

function brief(c) {
  const out = [];
  if (c.wx.length) out.push(c.wx.map(phrase).join(" and "));
  if (c.vis_sm != null && c.vis_sm < 3) out.push(`visibility ${c.vis} mi`);
  if (c.ceiling_ft != null && c.ceiling_ft <= 3000) out.push(`${c.ceiling_ft < 1000 ? "low cloud" : "cloud"} at ${c.ceiling_ft.toLocaleString("en-US")} ft`);
  if (c.wind_kt != null && (c.wind_kt >= 13 || c.gust_kt)) out.push(`wind ${c.wind_dir == null ? "variable" : COMPASS[pyRound(c.wind_dir / 22.5) % 16]} ${mph(c.wind_kt)} mph${c.gust_kt ? ` gusting ${mph(c.gust_kt)}` : ""}`);
  if (c.shear) out.push("wind shear aloft");
  return out.length ? out.join(", ") : "fair";
}

/** A TAF's periods: the base and each FM or BECMG change as main periods, TEMPO and PROB groups attached to the one
 * they fall in, all in local minutes. */
export function tafPeriods(text, task) {
  const tokens = text.replace(/=\s*$/, "").split(/\s+/).filter(Boolean);
  let i = tokens.findIndex((tok) => /^\d{4}\/\d{4}$/.test(tok));
  if (i < 0) return [];
  const span = (tok) => {
    const [a, b] = tok.split("/");
    return [zulu(task, +a.slice(0, 2), +a.slice(2, 4)), zulu(task, +b.slice(0, 2), +b.slice(2, 4))];
  };
  const [from, to] = span(tokens[i]);
  const groups = [{ kind: "base", from, to, tokens: [] }];
  for (i += 1; i < tokens.length; i++) {
    const tok = tokens[i];
    let m;
    if ((m = /^FM(\d{2})(\d{2})(\d{2})$/.exec(tok))) groups.push({ kind: "FM", from: zulu(task, +m[1], +m[2], +m[3]), to, tokens: [] });
    else if (/^(TEMPO|BECMG|PROB\d{2})$/.test(tok)) {
      let kind = tok, prob = null;
      if (tok.startsWith("PROB")) {
        prob = +tok.slice(4);
        if (tokens[i + 1] === "TEMPO") i++;
        kind = "PROB";
      }
      const [a, b] = /^\d{4}\/\d{4}$/.test(tokens[i + 1] ?? "") ? span(tokens[++i]) : [groups.at(-1).from, groups.at(-1).to];
      groups.push({ kind, prob, from: a, to: b, tokens: [] });
    } else groups.at(-1).tokens.push(tok);
  }
  const main = [];
  for (const g of groups) {
    if (g.kind === "base" || g.kind === "FM" || g.kind === "BECMG") {
      const was = main.at(-1)?.c, c = conditions(g.tokens);
      if (g.kind === "BECMG" && was) for (const k of ["wind_kt", "wind_dir", "gust_kt", "vis_sm", "ceiling_ft"]) c[k] ??= was[k];
      if (g.kind === "BECMG" && was && !c.wx.length && !c.nsw) c.wx = was.wx;
      if (main.length) main.at(-1).to = g.from;
      main.push({ from: g.from, to, c, extra: [] });
    } else (main.findLast((p) => p.from <= g.from) ?? main[0])?.extra.push({ ...g, c: conditions(g.tokens) });
  }
  return main;
}

/** A TAF in one line: the next hours from ``t``, at most ``most`` periods. */
export function taf(text, { task, t, hours = 12, most = 3 }) {
  const periods = tafPeriods(text, task).filter((p) => p.to > t && p.from < t + hours * 60).slice(0, most);
  return periods.map((p, k) => {
    const extra = p.extra.filter((x) => x.to > t && x.from < t + hours * 60 && (x.c.wx.length || x.c.cb))
      .map((x) => `${x.kind === "PROB" ? `${x.prob}% chance of ` : ""}${x.c.wx.length ? x.c.wx.map(phrase).join(" and ") : "thunderstorms"} ${x.kind === "PROB" ? "" : "at times "}${clock(Math.max(x.from, t))}–${clock(x.to)}`);
    const what = [brief(p.c), ...extra].join(", ");
    return k === 0 ? `Until ${clock(periods[1]?.from ?? p.to)}: ${what}` : `from ${clock(p.from)}: ${what}`;
  }).join("; ");
}

const place = (code, ctx) => HUBS[code] ?? ctx.city?.(code) ?? code;
const until = (m) => clock(m % 60 === 59 ? m + 1 : m);
const condition = (c) => {
  if (!c) return "";
  const key = c.includes("/") ? c.split("/")[1].trim() : c.trim();
  return (CONDITIONS[key] ?? key.toLowerCase().replace(/-/g, " and ")).replace(/^thunderstorms$/, "thunderstorms");
};
const rates = (r) => (r?.length ? (Math.min(...r) === Math.max(...r) ? `${r[0]} arrivals an hour` : `${Math.min(...r)} to ${Math.max(...r)} arrivals an hour`) : "");
const comments = (text) => (text.match(/(?:COMMENTS|REMARKS):([\s\S]*)$/)?.[1] ?? "").replace(/\s+/g, " ").trim();

function why(n) {
  const said = comments(n.text);
  if (/CARRIER REQUEST.*GATE/.test(said)) return "at JetBlue's request: its gates are full";
  if (/LACK OF ROUTES|NO ROUTES/.test(said)) return "storms block the routes in";
  if (/DIVERSION AIRCRAFT/.test(said)) return "diverted aircraft are crowding the ground";
  return condition(n.data?.condition);
}

/** An FAA Command Center advisory in plain English: {title, text, tone} with tone stop, gdp, clear or info.
 * ``earlier`` is the advisories issued before it, which tell an extension or a revision from a new program. */
export function advisory(n, ctx = {}) {
  const d = n.data ?? {}, where = place(d.element, ctx), cause = why(n);
  const jbu = /JBU/.test(d.flt_incl ?? "") || /^JBU /.test(d.title ?? "");
  const before = (ctx.earlier ?? []).filter((e) => e.kind === "advisory" && e.data?.element === d.element && e.at <= n.at && e !== n);
  const tail = (s) => (cause ? `${s} (${cause}).` : `${s}.`);
  if (d.kind === "gs") {
    const [a, b] = d.gs_period ?? [n.at, n.at];
    const prev = before.findLast((e) => e.data.kind === "gs" && e.data.gs_period && e.data.gs_period[1] >= a - 1 && e.data.gs_period[1] <= b
      && !before.some((x) => x.data.kind === "gs_cnx" && x.at > e.at));
    const who = jbu ? `JetBlue flights to ${where}` : `flights to ${where}`;
    const odds = d.probability_of_extension ? ` Chance it is extended: ${d.probability_of_extension.toLowerCase()}.` : "";
    if (prev?.data.gs_period[1] === b && n.at - prev.at >= 20) return { title: `Ground stop at ${where} continues until ${clock(b)}`, text: tail(`The ground stop goes on: ${who} wait at their departure airports until ${clock(b)}`) + odds, tone: "stop" };
    return prev && prev.data.gs_period[1] < b
      ? { title: `Ground stop at ${where} extended to ${clock(b)}`, text: tail(`The FAA extends the ground stop: ${who} stay at their departure airports until ${clock(b)}`) + odds, tone: "stop" }
      : { title: `Ground stop at ${where} until ${clock(b)}`, text: tail(`The FAA stops ${who}: they wait at their departure airports from ${clock(a)} to ${clock(b)}`) + odds, tone: "stop" };
  }
  if (d.kind === "gs_cnx") {
    return { title: `Ground stop at ${where} lifted`, text: `The FAA lifts the ${jbu ? "JetBlue " : ""}ground stop at ${where}: held flights leave on their assigned times.`, tone: "clear" };
  }
  if (d.kind === "gdp" || d.kind === "gdp_proposed") {
    const [a, b] = d.program_period ?? d.proposed_period ?? d.arrivals_for ?? [n.at, n.at];
    const revised = d.kind === "gdp" && before.some((e) => e.data.kind === "gdp" && !before.some((x) => x.data.kind === "gdp_cnx" && x.at > e.at));
    const delay = d.avg_delay != null ? `; average delay ${duration(d.avg_delay)}${d.max_delay ? `, up to ${duration(d.max_delay)}` : ""}` : "";
    if (d.kind === "gdp_proposed") {
      return { title: `Delay program proposed for ${where}`, text: tail(`The FAA proposes slowing arrivals at ${where} to ${rates(d.program_rate)} from ${clock(a)} to ${until(b)}${delay}`), tone: "gdp" };
    }
    return { title: `${revised ? "Delay program revised" : "Delay program"} at ${where}`,
      text: tail(`${revised ? "The FAA revises its ground delay program" : "The FAA starts a ground delay program"} at ${where}: flights to it get departure times, ${rates(d.program_rate)} until ${until(b)}${delay}`), tone: "gdp" };
  }
  if (d.kind === "gdp_cnx") {
    return { title: `Delay program at ${where} ends`, text: `The FAA ends the ground delay program at ${where}: flights to it no longer wait for a slot.`, tone: "clear" };
  }
  const title = d.title ?? n.text.split("\n")[0];
  if (/ARRIVAL DELAYS/.test(title)) {
    const up = /UP TO (\d+) MINUTES/.exec(n.text.replace(/\s+/g, " "));
    const due = /DUE TO ([^.]+)\./.exec(n.text.replace(/\s+/g, " "))?.[1].toLowerCase().replace(/ and\/or /g, " or ");
    return { title: `Arrival delays at ${where}`, text: `The FAA warns of arrival delays at ${where}${up ? ` of up to ${up[1]} minutes, circling in the air` : ", with circling in the air"}${due ? `, due to ${due}` : ""}.`, tone: "info" };
  }
  if (/DIVERSION RECOVERY/.test(title)) {
    return { title: `Diversion recovery at ${where}`, text: `The FAA helps flights that diverted elsewhere get back to ${where} first.`, tone: "info" };
  }
  return { title: upper(title.toLowerCase().replace(/^cdm /, "")), text: upper(title.toLowerCase()) + ".", tone: "info" };
}

/** An NWS forecast discussion's aviation section: the first sentences said about the hub (else the area), its UTC
 * times in local time and its flight categories in words. */
export function outlook(n, { task }) {
  const paras = n.text.replace(/\r/g, "").split(/\n\s*\n/).map((p) => p.replace(/\s+/g, " ").trim()).filter(Boolean);
  const k = paras.findIndex((p) => p.toUpperCase().startsWith(`K${task.hub} TAF`));
  const said = (p) => p && p.length > 40 && !/^\.AVIATION|Confidence Levels|percent\.$|^\d+z (TAF )?update|^Outlook|:$|^K[A-Z]{3} TAF|^[A-Z/()]+\) TAF/i.test(p)
    && !(/confidence/i.test(p) && p.length < 140);
  const own = k < 0 ? null : [paras[k].replace(/^K[A-Z]{3} TAF\.\.\.\s*/i, ""), paras[k + 1]].find((p) => said(p) && !/^K[A-Z]{3} TAF/i.test(p ?? ""));
  const body = own ?? paras.find(said) ?? "";
  const after = n.at - 180;
  const local = (h, mm, from = after) => {
    let m = zulu(task, Number(task.date.slice(8, 10)), +h, +(mm ?? 0));
    while (m < from) m += 1440;
    return m;
  };
  let text = body.replace(/\b(\d{2})(\d{2})?\s?z?\s?(?:-|to)\s?(\d{2})(\d{2})?\s?z\b/gi, (_, h1, m1, h2, m2) => {
    const a = local(h1, m1);
    return `${clock(a)} to ${clock(local(h2, m2, a))}`;
  }).replace(/\b(\d{2})(\d{2})?\s?z\b/gi, (_, h, mm) => clock(local(h, mm)));
  text = text.replace(/\([^)]*\b(?:MVFR|VFR|IFR)\b[^)]*\)/g, "").replace(/\b(?:L?IFR|MVFR|VFR)(?:\s*(?:\/|to|or)\s*(?:L?IFR|MVFR|VFR))*(?:\s+(?:cigs|ceilings|conditions))?\b/gi,
    (m) => (/IFR/i.test(m) ? "low cloud" : /MVFR/i.test(m) ? "lowish cloud" : "clear flying weather"));
  for (const [re, to] of JARGON) text = text.replace(re, to);
  text = text.replace(/\b(low cloud|lowish cloud|clear flying weather) are\b/gi, "$1 is").replace(/\s+([.,;])/g, "$1");
  const sentences = (text.match(/[^.!?]+[.!?]+/g) ?? [text]).map((x) => upper(x.trim()))
    .filter((x) => !/\b[A-Z]{3}\/[A-Z]{3}\b|\bK[A-Z]{3}\b/.test(x) && !/^(very |lowish )?(low cloud|clear flying weather)\.$/i.test(x));
  let out = "";
  for (const x of sentences) {
    if (out && out.length + x.length > 220) break;
    out += `${out ? " " : ""}${x}`;
  }
  return upper(out) || "Forecasters discuss the flying weather for the next hours.";
}

/** Any news item as {label, title, text, tone}: what the desk card shows, the original one click away. */
export function news(n, ctx) {
  if (n.kind === "advisory") return { label: "FAA", ...advisory(n, ctx) };
  if (n.kind === "metar") return { label: "Weather now", title: "Weather now", text: `${metar(n.text).plain}.`, tone: "wx" };
  if (n.kind === "taf") {
    const line = taf(n.text, { task: ctx.task, t: n.at, most: 2 });
    return { label: "Forecast", title: n.data?.amendment ? "Forecast amended" : "Forecast", text: line ? `${line}.` : "A new forecast.", tone: "wx" };
  }
  if (n.kind === "outlook") return { label: "Forecasters", title: n.speaker ?? "NWS forecast discussion", text: outlook(n, ctx), tone: "wx" };
  return { label: n.speaker ?? n.kind, title: n.speaker ?? n.kind, text: n.text.split("\n")[0], tone: "info" };
}

/** A flight as people say it: "B6 287 to Los Angeles", "B6 288 from Los Angeles", "B6 2554 Washington to Orlando". */
export function flightName(s, { hub, city = (c) => c }) {
  if (s.orig === hub) return `${s.flight} to ${city(s.dest)}`;
  if (s.dest === hub) return `${s.flight} from ${city(s.orig)}`;
  return `${s.flight} ${city(s.orig)} to ${city(s.dest)}`;
}

/** One change at a watch: "Cancelled B6 287 to Los Angeles (131 passengers)", "Held B6 1355 to Washington until 20:00". */
export function action(c, { sched, hub, city }) {
  const s = { flight: c.flight, orig: c.orig, dest: c.dest, ...sched?.get?.(c.id) }, name = flightName(s, { hub, city });
  if (c.action === "cancel") return `Cancelled ${name}${s.pax ? ` (${s.pax.toLocaleString("en-US")} passengers)` : ""}`;
  if (c.action === "hold") return `Held ${name} until ${clock(c.not_before)}`;
  if (c.action === "restore") return `Brought back ${name}${c.not_before != null ? `, not before ${clock(c.not_before)}` : ""}`;
  return `Let ${name} leave as soon as it can`;
}

const short = (n) => (n == null ? "–" : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : Math.round(n).toLocaleString("en-US"));
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

function changeList(changes) {
  const byKind = {};
  for (const c of changes ?? []) (byKind[c.action] ??= []).push(c);
  const said = [];
  const named = (cs) => (cs.length > 3 ? `${cs.slice(0, 3).map((c) => c.flight).join(", ")} and ${cs.length - 3} more` : and(cs.map((c) => c.flight)));
  if (byKind.cancel) said.push(`cancel ${named(byKind.cancel)}`);
  if (byKind.hold) said.push(`hold ${named(byKind.hold)}`);
  if (byKind.release) said.push(`release ${named(byKind.release)}`);
  if (byKind.restore) said.push(`bring back ${named(byKind.restore)}`);
  return and(said);
}

/** A tool call in words, e.g. "Tried a plan: cancel B6 287 and B6 1055: it would cost 1.97M passenger-minutes". */
export function step(s, ctx = {}) {
  const changes = (Array.isArray(s.args?.changes) ? s.args.changes : []).map((c) => ({ action: c.action === "hold" && c.not_before == null ? "release" : c.action, flight: ctx.flightOf?.(c.flight) ?? c.flight }));
  const r = s.result ?? {};
  if (r.error) return `Its call to ${s.tool} was refused: ${String(r.error).slice(0, 120)}`;
  if (s.tool === "get_situation") return "Read the situation: the news, the plan and what it costs";
  if (s.tool === "advance") return r.done ? "Closed the desk: the rest of the day is flown on what really happened" : "Handed over the watch: the clock moves on an hour";
  if (s.tool === "check_plan") {
    const what = changeList(changes);
    return `Tried ${what ? `a plan to ${what}` : "the plan as it stands"}: the day as known would cost ${short(r.cost?.total)} passenger-minutes`;
  }
  if (s.tool === "confirm_changes") {
    const n = r.confirmed?.length ?? changes.length;
    return n ? `Confirmed ${plural(n, "change")}: ${changeList(changes)}` : "Confirmed no changes";
  }
  return s.tool;
}
