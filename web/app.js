// Renders the league page from the data build.py embeds. This is a port of the
// render_* functions in app.py. Anything that depends on the current time runs
// here in the browser, so "Tonight" and "Score not posted" stay right between builds.
(() => {
"use strict";

const D = JSON.parse(document.getElementById("league-data").textContent);
const ALL = "All teams";
const DAY = 86400000;
const GAME_LENGTH = 60 * 60000;
const REBUILD_MINUTES = 15;
const STALE_AFTER = 6 * 3600000;
const params = new URLSearchParams(location.search);
const $ = (id) => document.getElementById(id);

const e = (s) => String(s ?? "").replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#x27;" })[c]);

// ---------------------------------------------------------------- time
// All times are league-local wall-clock values stored as UTC milliseconds,
// which keeps date math free of the viewer's own timezone.
const dayMs = (s) => Date.UTC(+s.slice(0, 4), s.slice(5, 7) - 1, +s.slice(8, 10));
const minutes = (hm) => (hm ? +hm.slice(0, 2) * 60 + +hm.slice(3, 5) : null);
const startMs = (g) => dayMs(g.date) + (g.time ? minutes(g.time) : 20 * 60) * 60000;
const floorDay = (ms) => Math.floor(ms / DAY) * DAY;

function leagueNow() {
  // ?now=2026-10-01T20:45 previews the page as of that moment (like LEAGUE_NOW)
  const m = (params.get("now") || "").match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/);
  if (m) return Date.UTC(+m[1], m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0));
  const p = {};
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: D.tz, hourCycle: "h23",
    year: "numeric", month: "numeric", day: "numeric", hour: "numeric", minute: "numeric",
  });
  for (const x of fmt.formatToParts(new Date())) p[x.type] = x.value;
  return Date.UTC(+p.year, p.month - 1, +p.day, +p.hour % 24, +p.minute);
}

// ---------------------------------------------------------------- formatting
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function fmtTime(hm) {
  if (!hm) return "TBD";
  const h = +hm.slice(0, 2);
  return `${h % 12 || 12}:${hm.slice(3, 5)} ${h < 12 ? "AM" : "PM"}`;
}

function fmtDay(s) {
  const d = new Date(dayMs(s));
  return `${DOW[d.getUTCDay()]}, ${MON[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

function relative(s, today) {
  const n = Math.round((dayMs(s) - today) / DAY);
  if (n === 0) return "Tonight";
  if (n === 1) return "Tomorrow";
  return n > 1 && n < 7 ? `In ${n} days` : "";
}

const ordinal = (n) =>
  n + (n % 100 >= 10 && n % 100 <= 20 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" })[n % 10] || "th");

// ---------------------------------------------------------------- model
const has = (x, team) => x.t1 === team || x.t2 === team;
const opponent = (g, team) => (g.t1 === team ? g.t2 : g.t1);
const winner = (r) => (r.p1 >= r.p2 ? r.t1 : r.t2);
const resultOf = (g) => (g.result == null ? null : D.results[g.result]);

function resolveTeam(name) {
  const n = (name || "").trim().toLowerCase();
  if (!n) return null;
  const exact = D.teams.find((t) => t.toLowerCase() === n);
  if (exact) return exact;
  const pref = n.length >= 3 ? D.teams.filter((t) => t.toLowerCase().startsWith(n)) : [];
  return pref.length === 1 ? pref[0] : null;
}

// ---------------------------------------------------------------- pieces
function matchupHtml(g, team) {
  if (g.kind === "off") return '<span class="lg-muted">No games</span>';
  if (g.kind === "tbd") {
    const lbl = /playoff/i.test(g.label) ? "Playoff game" : g.label;
    return `${e(lbl)} <span class="lg-muted">(teams TBD)</span>`;
  }
  if (team) return `<span class="vs">vs</span> ${e(opponent(g, team))}`;
  return `${e(g.t1)} <span class="vs">vs</span> ${e(g.t2)}`;
}

function scoreHtml(r, team) {
  const w = winner(r);
  const rows = [[r.t1, r.p1], [r.t2, r.p2]]
    .sort((a, b) => b[1] - a[1])
    .map(([t, p]) => {
      const cls = t === w ? " won" : "";
      return `<div class="t${cls}">${e(t)}</div><div class="p${cls}">${p}</div>`;
    })
    .join("");
  let tags = "";
  if (team) {
    const won = w === team;
    tags += `<span class="lg-tag ${won ? "w" : ""}">${won ? "W" : "L"}</span> `;
  }
  if (r.note) tags += `<span class="lg-tag ot">${e(r.note)}</span>`;
  return [`<div class="lg-score">${rows}</div>`, tags];
}

function dayHeader(d, today, notes) {
  let out = `<div class="lg-day"><span class="lg-dayname">${e(fmtDay(d))}</span>` +
    `<span class="lg-rel">${e(relative(d, today))}</span></div>`;
  for (const n of notes[d] || []) out += `<div class="lg-note">Note: ${e(n)}</div>`;
  return out;
}

// ---------------------------------------------------------------- sections
function renderHero(team, now) {
  const today = floorDay(now);
  const upcoming = D.games.filter(
    (g) => g.kind !== "off" && startMs(g) + GAME_LENGTH > now && g.result == null);
  if (team) {
    let div = null, rank = null, row = null;
    for (const [d, rows] of D.standings) {
      const i = rows.findIndex((x) => x.team === team);
      if (i >= 0) { div = d; rank = i + 1; row = rows[i]; break; }
    }
    const nxt = upcoming.find((g) => has(g, team) || g.kind === "tbd");
    const last = [...D.results].sort((a, b) => b.date.localeCompare(a.date)).find((r) => has(r, team));
    const lines = [];
    if (row) lines.push(`${row.w}–${row.l}, ${ordinal(rank)} in ${e(div)}`);
    if (nxt) {
      const vs = nxt.kind === "game" ? `vs ${e(opponent(nxt, team))}` : "matchup TBD";
      lines.push(`<b>Next:</b> ${e(fmtDay(nxt.date))}, ${e(fmtTime(nxt.time))} ${vs}`);
    } else {
      lines.push("No more games scheduled.");
    }
    if (last) {
      const verb = winner(last) === team ? "Beat" : "Lost to";
      const opp = last.t1 === team ? last.t2 : last.t1;
      const ot = last.note.toLowerCase().includes("ot") ? " in OT" : "";
      lines.push(`<b>Last:</b> ${verb} ${e(opp)} ${Math.max(last.p1, last.p2)}–${Math.min(last.p1, last.p2)}${ot}, ${e(fmtDay(last.date))}`);
    }
    const rel = nxt ? relative(nxt.date, today) : "";
    const kicker = nxt && has(nxt, team) && (rel === "Tonight" || rel === "Tomorrow") ? rel : "Your team";
    return `<div class="lg-hero"><div class="lg-kicker">${e(kicker)}</div><div class="lg-big">${e(team)}</div>` +
      lines.map((x) => `<div class="lg-hline">${x}</div>`).join("") + "</div>";
  }
  if (!upcoming.length) {
    return '<div class="lg-hero"><div class="lg-kicker">Season complete</div>' +
      '<div class="lg-big">That’s a wrap</div><div class="lg-hline">Final scores and standings are below.</div></div>';
  }
  const d = upcoming[0].date;
  const rows = D.games
    .filter((g) => g.date === d && g.kind !== "off")
    .map((g) => `<div class="lg-hrow"><span class="lg-htime">${e(fmtTime(g.time))}</span>` +
      `<span class="lg-hmatch">${matchupHtml(g, null)}</span></div>`)
    .join("");
  return `<div class="lg-hero"><div class="lg-kicker">${e(relative(d, today) || "Up next")}</div>` +
    `<div class="lg-big">${e(fmtDay(d))}</div>${rows}</div>`;
}

function renderSchedule(team, now, showPast) {
  const today = floorDay(now);
  const byDay = new Map();
  for (const g of D.games) {
    if (!showPast && dayMs(g.date) < today) continue;
    if (!byDay.has(g.date)) byDay.set(g.date, []);
    byDay.get(g.date).push(g);
  }
  let out = "";
  for (const d of [...byDay.keys()].sort()) {
    let games = byDay.get(d);
    if (team) {
      const mine = games.filter((g) => has(g, team));
      const tbd = games.filter((g) => g.kind === "tbd");
      if (mine.length) {
        games = mine;
      } else if (tbd.length) {
        const times = tbd.map((g) => fmtTime(g.time).replace(" PM", "")).join(" & ") + " PM";
        const lbl = tbd[0].stage ? "Playoff games" : "League games";
        out += dayHeader(d, today, D.notes) +
          `<div class="lg-row"><span class="lg-time">${e(times)}</span>` +
          `<span class="lg-match">${lbl} <span class="lg-muted">(matchups TBD)</span></span><span></span></div>`;
        continue;
      } else if (games.every((g) => g.kind === "off")) {
        games = games.slice(0, 1);
      } else {
        continue; // team is off this night
      }
    } else if (games.every((g) => g.kind === "off")) {
      games = games.slice(0, 1);
    }
    out += dayHeader(d, today, D.notes);
    for (const g of games) {
      const t = g.kind === "off" ? "" : e(fmtTime(g.time));
      const r = resultOf(g);
      if (r) {
        const [sc, tags] = scoreHtml(r, team);
        out += `<div class="lg-row"><span class="lg-time">${t}</span>${sc}<span>${tags}</span></div>`;
      } else {
        const right = g.kind === "game" && startMs(g) + GAME_LENGTH < now
          ? '<span class="lg-tag">Score not posted</span>' : "";
        const stage = g.stage && g.kind !== "off" && !right ? `<span class="lg-tag">${e(g.stage)}</span>` : "";
        out += `<div class="lg-row"><span class="lg-time">${t}</span>` +
          `<span class="lg-match">${matchupHtml(g, team)}</span><span>${right || stage}</span></div>`;
      }
    }
  }
  return out || '<div class="lg-empty">No games left on the schedule. Turn on “Show played games” to see the season.</div>';
}

function renderScores(team, today) {
  const res = D.results.filter((r) => !team || has(r, team));
  if (!res.length) {
    return '<div class="lg-empty">No scores posted yet. Results show up here after each game night.</div>';
  }
  const byDay = new Map();
  for (const r of res) {
    if (!byDay.has(r.date)) byDay.set(r.date, []);
    byDay.get(r.date).push(r);
  }
  let out = "";
  for (const d of [...byDay.keys()].sort().reverse()) {
    out += dayHeader(d, today, {});
    for (const r of byDay.get(d).sort((a, b) => (minutes(a.time) ?? 0) - (minutes(b.time) ?? 0))) {
      const [sc, tags] = scoreHtml(r, team);
      out += `<div class="lg-row"><span class="lg-time">${r.time ? e(fmtTime(r.time)) : ""}</span>${sc}<span>${tags}</span></div>`;
    }
  }
  return out;
}

function renderStandings(team) {
  let out = "";
  for (const [div, rows] of D.standings) {
    out += `<div class="lg-div">${e(div)}</div><div class="lg-wrap"><table class="lg-tbl"><thead><tr>` +
      '<th scope="col">Team</th><th scope="col">W</th><th scope="col">L</th><th scope="col">GB</th>' +
      '<th scope="col" class="lg-pf">PF</th><th scope="col" class="lg-pf">PA</th>' +
      '<th scope="col">+/−</th><th scope="col">Streak</th></tr></thead><tbody>';
    for (const x of rows) {
      const gb = x.gb === 0 ? "—" : Number.isInteger(x.gb) ? String(x.gb) : x.gb.toFixed(1);
      const diff = x.diff > 0 ? `+${x.diff}` : x.diff < 0 ? `−${-x.diff}` : "0";
      out += `<tr${x.team === team ? ' class="mine"' : ""}><th scope="row">${e(x.team)}</th>` +
        `<td>${x.w}</td><td>${x.l}</td><td>${gb}</td>` +
        `<td class="lg-pf">${x.pf}</td><td class="lg-pf">${x.pa}</td><td>${diff}</td>` +
        `<td>${e(x.streak) || "—"}</td></tr>`;
    }
    out += "</tbody></table></div>";
  }
  return out;
}

// ---------------------------------------------------------------- page
const sel = $("team");
const past = $("past");
sel.innerHTML = [ALL, ...D.teams].map((t) => `<option>${e(t)}</option>`).join("");
sel.value = resolveTeam(params.get("team")) || ALL;

function render() {
  const now = leagueNow();
  const team = sel.value === ALL ? null : sel.value;
  $("hero").innerHTML = renderHero(team, now);
  $("schedule").innerHTML = renderSchedule(team, now, past.checked);
  $("scores").innerHTML = renderScores(team, floorDay(now));
  $("standings").innerHTML = renderStandings(team);
}

sel.addEventListener("change", () => {
  if (sel.value === ALL) params.delete("team");
  else params.set("team", sel.value);
  const q = params.toString();
  history.replaceState(null, "", q ? `?${q}` : location.pathname);
  render();
});
past.addEventListener("change", render);

const tabs = [...document.querySelectorAll('[role="tab"]')];
function selectTab(tab, focus) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute("aria-selected", String(on));
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute("aria-controls")).hidden = !on;
  }
  if (focus) tab.focus();
}
tabs.forEach((t, i) => {
  t.addEventListener("click", () => selectTab(t));
  t.addEventListener("keydown", (ev) => {
    const step = { ArrowRight: 1, ArrowLeft: -1 }[ev.key];
    if (step) {
      ev.preventDefault();
      selectTab(tabs[(i + step + tabs.length) % tabs.length], true);
    }
  });
});

// Footer, plus a warning if the scheduled rebuild has stopped running
const f = D.fetched; // league-local ISO, e.g. 2026-09-24T20:30-04:00
const fetchedAt = `${fmtDay(f.slice(0, 10))}, ${fmtTime(f.slice(11, 16))}`;
const src = D.sheet ? `<a href="${e(D.sheet)}">league sheet</a>` : "league sheet";
$("foot").innerHTML = `Pulled from the ${src} every ${REBUILD_MINUTES} minutes. Last updated ${e(fetchedAt)}.`;
const fetchedMs = dayMs(f) + minutes(f.slice(11, 16)) * 60000;
if (!params.has("now") && leagueNow() - fetchedMs > STALE_AFTER) {
  $("stale").textContent = `This page was last updated ${fetchedAt}, so recent changes to the league sheet may be missing.`;
  $("stale").hidden = false;
}

render();
// Keep "Tonight" and "Score not posted" current while the page is open, and
// pick up a newer build when someone comes back to a tab left open for a while.
const loadedAt = Date.now();
setInterval(render, 60000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && Date.now() - loadedAt > REBUILD_MINUTES * 60000) location.reload();
});
})();
