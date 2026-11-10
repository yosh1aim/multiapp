"use strict";

/* ---------- helpers ---------- */
const $ = (s, root = document) => root.querySelector(s);
const pad = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localDT = (d) => `${ymd(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const parseDay = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); };
const timeOf = (iso) => iso.slice(11, 16);
const MONTHS = ["January","February","March","April","May","June","July","August","September","October","November","December"];
const DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];

function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null) n.append(k.nodeType ? k : document.createTextNode(k));
  return n;
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.hidden = true), 2600);
}
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message); } };

document.querySelectorAll(".time-input").forEach((i) => TimeField.attach(i));

/* ---------- state ---------- */
const today = () => ymd(new Date());
const state = {
  month: new Date(new Date().getFullYear(), new Date().getMonth(), 1),
  selected: today(),
  events: [], reminders: [], notes: {},
};

/* ---------- tabs ---------- */
function showTab(name) {
  document.querySelectorAll(".nav-btn[data-tab]").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === `tab-${name}`));
  if (name === "claude") initChat();
  if (name === "reminders") loadAll();
  if (name === "notes") loadPages(); else flushPage();
  if (name === "progress") guard(loadProgress)();
}
document.querySelectorAll(".nav-btn[data-tab]").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

/* ---------- data loading ---------- */
function gridRange() {
  const first = new Date(state.month);
  const offset = (first.getDay() + 6) % 7; // Monday-first
  const start = new Date(first.getFullYear(), first.getMonth(), 1 - offset);
  const end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 41);
  return [start, end];
}

async function loadAll() {
  const [start, end] = gridRange();
  const [events, reminders, notes] = await Promise.all([
    api(`/api/events?start=${ymd(start)}&end=${ymd(end)}`),
    api("/api/reminders"),
    api(`/api/notes?start=${ymd(start)}&end=${ymd(end)}`),
  ]);
  state.events = events;
  state.reminders = reminders;
  state.notes = Object.fromEntries(notes.map((n) => [n.date, n.body]));
  renderCalendar(); renderDayPanel(); renderUpcoming(); renderReminders();
}

/* ---------- calendar ---------- */
$("#cal-dow").append(...DOW.map((d) => el("div", {}, d)));

const eventsOn = (d) => state.events.filter((e) => e.start.slice(0, 10) === d);
const remsOn = (d) => state.reminders.filter((r) => !r.done && r.due_at.slice(0, 10) === d);

function renderCalendar() {
  $("#cal-title").textContent = `${MONTHS[state.month.getMonth()]} ${state.month.getFullYear()}`;
  const [start] = gridRange();
  const grid = $("#cal-grid");
  grid.replaceChildren();
  for (let i = 0; i < 42; i++) {
    const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + i);
    const key = ymd(d);
    const evs = eventsOn(key), rems = remsOn(key);
    const chips = [
      ...evs.map((e) => el("div", { class: "chip event" }, (e.all_day ? "" : timeOf(e.start) + " ") + e.title)),
      ...rems.map((r) => el("div", { class: "chip rem" }, "🔔 " + r.text)),
    ];
    const MAX = 3;
    const cell = el("button", {
      class: ["day", d.getMonth() !== state.month.getMonth() && "other", key === today() && "today", key === state.selected && "selected"].filter(Boolean).join(" "),
      onclick: () => selectDay(key),
      "aria-label": key,
    },
      el("div", { class: "day-num" }, el("span", {}, d.getDate()), state.notes[key] ? el("span", { class: "note-dot", title: "Has a note" }, "📝") : null),
      chips.slice(0, MAX),
      chips.length > MAX ? el("div", { class: "more" }, `+${chips.length - MAX} more`) : null,
    );
    grid.append(cell);
  }
}

let noteTimer, noteDirtyFor = null;
async function flushNote() {
  clearTimeout(noteTimer);
  if (noteDirtyFor === null) return;
  const date = noteDirtyFor; noteDirtyFor = null;
  const body = $("#day-note").dataset.date === date ? $("#day-note").value : state.notes[date] || "";
  try {
    await api(`/api/notes/${date}`, { method: "PUT", body: { body } });
    if (body.trim()) state.notes[date] = body; else delete state.notes[date];
    $("#note-status").textContent = "Saved";
    renderCalendar();
  } catch (e) { $("#note-status").textContent = "Couldn't save: " + e.message; }
}

async function selectDay(key) {
  await flushNote();
  state.selected = key;
  const d = parseDay(key);
  if (d.getMonth() !== state.month.getMonth() || d.getFullYear() !== state.month.getFullYear()) {
    state.month = new Date(d.getFullYear(), d.getMonth(), 1);
    await loadAll();
  } else { renderCalendar(); renderDayPanel(); }
}

function renderDayPanel() {
  const key = state.selected, d = parseDay(key);
  $("#day-title").textContent = d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  $("#day-sub").textContent = key === today() ? "Today" : "";
  const box = $("#day-items");
  box.replaceChildren();
  const evs = eventsOn(key), rems = remsOn(key);
  evs.forEach((e) => box.append(el("div", { class: "item", onclick: () => openEvent(e) },
    el("div", { class: "bar" }),
    el("div", {}, el("div", { class: "t" }, e.title),
      el("div", { class: "s" }, [e.all_day ? "All day" : timeOf(e.start) + (e.end ? "–" + timeOf(e.end) : ""), e.location].filter(Boolean).join(" · "))),
    el("button", { class: "icon-btn x", title: "Delete event", "aria-label": "Delete event",
      onclick: (ev) => { ev.stopPropagation(); deleteEvent(e); } }, "✕"))));
  rems.forEach((r) => box.append(el("div", { class: "item rem", onclick: () => showTab("reminders") },
    el("div", { class: "bar" }),
    el("div", {}, el("div", { class: "t" }, "🔔 " + r.text), el("div", { class: "s" }, "Reminder · " + timeOf(r.due_at))))));
  if (!evs.length && !rems.length) box.append(el("div", { class: "empty" }, "Nothing scheduled."));
  const note = $("#day-note");
  note.value = state.notes[key] || "";
  note.dataset.date = key;
  $("#note-status").textContent = "";
}

$("#day-note").addEventListener("input", () => {
  noteDirtyFor = $("#day-note").dataset.date;
  $("#note-status").textContent = "Typing…";
  clearTimeout(noteTimer);
  noteTimer = setTimeout(flushNote, 700);
});
$("#day-note").addEventListener("blur", flushNote);
window.addEventListener("beforeunload", () => { if (noteDirtyFor) flushNote(); });

function renderUpcoming() {
  const box = $("#upcoming");
  box.replaceChildren();
  const now = new Date(), horizon = ymd(new Date(now.getFullYear(), now.getMonth(), now.getDate() + 14));
  const items = [
    ...state.events.filter((e) => e.start.slice(0, 10) >= today() && e.start.slice(0, 10) <= horizon)
      .map((e) => ({ when: e.all_day ? e.start + "T00:00" : e.start, label: e.title, kind: "event", allDay: e.all_day })),
    ...state.reminders.filter((r) => !r.done && r.due_at.slice(0, 10) >= today() && r.due_at.slice(0, 10) <= horizon)
      .map((r) => ({ when: r.due_at, label: r.text, kind: "rem" })),
  ].sort((a, b) => a.when.localeCompare(b.when)).slice(0, 8);
  if (!items.length) { box.append(el("div", { class: "empty" }, "No events or reminders in the next 2 weeks.")); return; }
  for (const it of items) {
    const d = parseDay(it.when.slice(0, 10));
    box.append(el("div", { class: "up-row" },
      el("span", { class: "up-date" }, d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })),
      el("span", { class: "chip " + it.kind }, (it.kind === "rem" ? "🔔 " : "") + (it.allDay ? "" : timeOf(it.when) + " ") + it.label)));
  }
}

$("#cal-prev").onclick = guard(async () => { await flushNote(); state.month = new Date(state.month.getFullYear(), state.month.getMonth() - 1, 1); await loadAll(); });
$("#cal-next").onclick = guard(async () => { await flushNote(); state.month = new Date(state.month.getFullYear(), state.month.getMonth() + 1, 1); await loadAll(); });
$("#cal-today").onclick = guard(async () => {
  await flushNote();
  const n = new Date(); state.month = new Date(n.getFullYear(), n.getMonth(), 1); state.selected = today(); await loadAll();
});

const deleteEvent = guard(async (e) => {
  if (!confirm(`Delete “${e.title}”? Its reminder is removed too.`)) return;
  await api(`/api/events/${e.id}`, { method: "DELETE" });
  await loadAll(); toast("Event deleted");
});

/* ---------- event dialog ---------- */
const evDlg = $("#event-dialog");
let editingEvent = null;

function syncAllDay() {
  const allDay = $("#ev-allday").checked;
  $("#ev-start-time").hidden = $("#ev-end-time").hidden = allDay;
}
function openEvent(ev) {
  editingEvent = ev || null;
  $("#event-dialog-title").textContent = ev ? "Edit event" : "New event";
  $("#ev-title").value = ev ? ev.title : "";
  $("#ev-allday").checked = ev ? !!ev.all_day : false;
  const start = ev ? ev.start : `${state.selected}T09:00`;
  $("#ev-start-date").value = start.slice(0, 10);
  TimeField.set($("#ev-start-time"), ev && ev.all_day ? "" : start.slice(11, 16));
  $("#ev-end-date").value = ev && ev.end ? ev.end.slice(0, 10) : "";
  TimeField.set($("#ev-end-time"), ev && ev.end && !ev.all_day ? ev.end.slice(11, 16) : "");
  syncAllDay();
  $("#ev-location").value = ev ? ev.location : "";
  $("#ev-desc").value = ev ? ev.description : "";
  $("#ev-remind").value = String(ev ? ev.remind_min : 15);
  $("#ev-delete").hidden = !ev;
  evDlg.showModal();
  $("#ev-title").focus();
}
$("#add-event").onclick = () => openEvent(null);
$("#ev-allday").onchange = syncAllDay;
$("#ev-cancel").onclick = () => evDlg.close();
$("#event-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const allDay = $("#ev-allday").checked;
  const sDate = $("#ev-start-date").value, sTime = TimeField.get($("#ev-start-time"));
  if (!sDate) throw new Error("Pick a start date");
  if (!allDay && !sTime) throw new Error("Enter a start time, e.g. 1430");
  let eDate = $("#ev-end-date").value;
  const eTime = TimeField.get($("#ev-end-time"));
  if (!eDate && eTime && !allDay) eDate = sDate;
  const body = {
    title: $("#ev-title").value.trim(),
    start: allDay ? sDate : `${sDate}T${sTime}`,
    end: eDate ? (allDay ? eDate : `${eDate}T${eTime || sTime}`) : null,
    all_day: allDay,
    location: $("#ev-location").value.trim(),
    description: $("#ev-desc").value.trim(),
    remind_min: Number($("#ev-remind").value),
  };
  if (editingEvent) await api(`/api/events/${editingEvent.id}`, { method: "PATCH", body });
  else await api("/api/events", { method: "POST", body });
  evDlg.close();
  state.selected = body.start.slice(0, 10);
  const d = parseDay(state.selected);
  state.month = new Date(d.getFullYear(), d.getMonth(), 1);
  await loadAll();
}));
$("#ev-delete").onclick = guard(async () => {
  if (!editingEvent || !confirm(`Delete “${editingEvent.title}”?`)) return;
  await api(`/api/events/${editingEvent.id}`, { method: "DELETE" });
  evDlg.close(); await loadAll();
});

/* ---------- reminders ---------- */
function resetReminderWhen() {            // next whole hour
  const d = new Date(Date.now() + 60 * 60 * 1000); d.setMinutes(0, 0, 0);
  $("#rem-date").value = ymd(d);
  TimeField.set($("#rem-time"), `${pad(d.getHours())}:00`);
}
resetReminderWhen();

$("#rem-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  const t = TimeField.get($("#rem-time"));
  if (!t) throw new Error("Enter a time, e.g. 1430");
  await api("/api/reminders", { method: "POST", body: { text: $("#rem-text").value.trim(), due_at: `${$("#rem-date").value}T${t}`, repeat: $("#rem-repeat").value } });
  $("#rem-text").value = ""; $("#rem-repeat").value = "none"; resetReminderWhen();
  await loadAll(); toast("Reminder added");
}));

function whenLabel(iso) {
  const d = new Date(iso), t = timeOf(iso), key = iso.slice(0, 10);
  const tmr = ymd(new Date(Date.now() + 864e5));
  if (key === today()) return `Today ${t}`;
  if (key === tmr) return `Tomorrow ${t}`;
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }) + " " + t;
}

function remRow(r, overdue) {
  const sub = whenLabel(r.due_at) + (r.repeat !== "none" ? ` · repeats ${r.repeat}` : "") + (r.sent_at && !r.done ? " · sent" : "");
  let evInfo = "";
  if (r.event_id && r.event_start) {
    const evWhen = r.event_all_day
      ? "all day " + parseDay(r.event_start.slice(0, 10)).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })
      : "starts " + whenLabel(r.event_start);
    evInfo = " · event " + evWhen + (r.event_location ? " · " + r.event_location : "");
  }
  return el("div", { class: "rem" + (r.done ? " done" : "") + (overdue ? " overdue" : "") },
    el("input", { type: "checkbox", checked: !!r.done, "aria-label": "Complete", onchange: guard(async () => {
      if (r.done) await api(`/api/reminders/${r.id}`, { method: "PATCH", body: { done: false } });
      else await api(`/api/reminders/${r.id}/complete`, { method: "POST" });
      await loadAll();
    }) }),
    el("div", { class: "body" },
      el("div", { class: "t" }, (r.event_id ? "📅 " : "") + r.text),
      el("div", { class: "s" }, sub + evInfo),
      r.event_id && r.event_description ? el("div", { class: "desc" }, r.event_description) : null),
    el("div", { class: "acts" },
      !r.done ? el("button", { class: "icon-btn", title: "Snooze 10 min", onclick: guard(async () => { await api(`/api/reminders/${r.id}/snooze`, { method: "POST", body: { minutes: 10 } }); await loadAll(); toast("Snoozed 10 min"); }) }, "+10m") : null,
      !r.done ? el("button", { class: "icon-btn", title: "Snooze 1 hour", onclick: guard(async () => { await api(`/api/reminders/${r.id}/snooze`, { method: "POST", body: { minutes: 60 } }); await loadAll(); toast("Snoozed 1 hour"); }) }, "+1h") : null,
      el("button", { class: "icon-btn", title: "Delete", "aria-label": "Delete", onclick: guard(async () => { await api(`/api/reminders/${r.id}`, { method: "DELETE" }); await loadAll(); }) }, "✕")));
}

function renderReminders() {
  const box = $("#rem-list");
  box.replaceChildren();
  const nowIso = localDT(new Date());
  const open = state.reminders.filter((r) => !r.done);
  const groups = [
    ["Overdue", open.filter((r) => r.due_at.slice(0, 16) < nowIso), "overdue"],
    ["Today", open.filter((r) => r.due_at.slice(0, 16) >= nowIso && r.due_at.slice(0, 10) === today()), ""],
    ["Upcoming", open.filter((r) => r.due_at.slice(0, 10) > today()), ""],
    ["Done", state.reminders.filter((r) => r.done).reverse().slice(0, 10), ""],
  ];
  let any = false;
  for (const [title, list, cls] of groups) {
    if (!list.length) continue;
    any = true;
    box.append(el("div", { class: "rem-group " + cls }, el("h3", {}, `${title} (${list.length})`), list.map((r) => remRow(r, cls === "overdue"))));
  }
  if (!any) box.append(el("div", { class: "empty" }, "No reminders yet. Add one above."));
  const badge = $("#badge-reminders"), overdue = groups[0][1].length;
  badge.hidden = !overdue; badge.textContent = overdue;
}

/* ---------- notes: free-form notes + checklists (Apple Notes style) ---------- */
const pages = { list: [], current: null, q: "" };
let pageTimer, pageDirty = false;
const pageById = (id) => pages.list.find((p) => p.id === id);
const isList = (p) => !!p && p.kind === "checklist";
const doneCount = (p) => p.items.filter((i) => i.done).length;
const pageTitle = (p) => isList(p)
  ? (p.body.trim() || "New checklist")
  : ((p.body.split("\n").find((l) => l.trim()) || "").trim() || "New note");
function pagePreview(p) {
  if (isList(p)) {
    if (!p.items.length) return "Empty checklist";
    return `${doneCount(p)}/${p.items.length} done` + (p.reset_mode === "daily" ? ` · resets ${p.reset_time}` : "");
  }
  return p.body.split("\n").map((l) => l.trim()).filter(Boolean)[1] || "No additional text";
}
const isBlank = (p) => isList(p)
  ? !p.body.trim() && p.items.every((i) => !i.text.trim() && !i.comment.trim())
  : !p.body.trim();
function pageStamp(iso) {
  return iso.slice(0, 10) === today() ? timeOf(iso)
    : new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
function touch(p) {   // newest-edited note floats to the top, like Apple Notes
  p.updated_at = localDT(new Date()) + ":00";
  pages.list = [p, ...pages.list.filter((x) => x.id !== p.id)];
  renderPageList();
}

function renderPageList() {
  const box = $("#notes-list");
  box.replaceChildren();
  if (!pages.list.length) {
    box.append(el("div", { class: "empty", style: "padding:12px" }, pages.q ? "No notes match your search." : "No notes yet. Add a note or a checklist."));
    return;
  }
  for (const p of pages.list) {
    box.append(el("button", { class: "note-item" + (p.id === pages.current ? " active" : ""), onclick: guard(() => selectPage(p.id)) },
      el("div", { class: "nt" }, isList(p) ? el("span", { class: "ico" }, "☑") : null, pageTitle(p)),
      el("div", { class: "ns" }, el("span", { class: "dt" }, pageStamp(p.updated_at)), el("span", { class: "pv" }, pagePreview(p)))));
  }
}

function renderPageEditor() {
  const p = pageById(pages.current), ta = $("#page-body"), list = isList(p);
  $("#checklist-editor").hidden = !list;
  ta.hidden = list;
  ta.disabled = !p;
  ta.value = p && !list ? p.body : "";
  $("#page-delete").hidden = !p;
  $("#page-meta").textContent = p
    ? "Edited " + new Date(p.updated_at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })
    : "Select a note, or create a new one.";
  if (list) renderChecklist(p);
}

/* --- saving --- */
const itemPending = new Map();   // item id -> { timer, fields, send }
function saveItem(it, fields, now = false) {
  const entry = itemPending.get(it.id) || { fields: {} };
  Object.assign(entry.fields, fields);
  clearTimeout(entry.timer);
  entry.send = async () => {
    itemPending.delete(it.id);
    try { await api(`/api/notebook/items/${it.id}`, { method: "PATCH", body: entry.fields }); }
    catch (e) { toast("Couldn't save item: " + e.message); }
  };
  if (now) entry.send();
  else { entry.timer = setTimeout(entry.send, 500); itemPending.set(it.id, entry); }
}
async function flushItems() {
  for (const e of [...itemPending.values()]) { clearTimeout(e.timer); await e.send(); }
}
function dropPending(id) { const e = itemPending.get(id); if (e) { clearTimeout(e.timer); itemPending.delete(id); } }

async function flushPage() {
  clearTimeout(pageTimer);
  await flushItems();
  if (!pageDirty) return;
  pageDirty = false;
  const p = pageById(pages.current);
  if (!p) return;
  try {
    const saved = await api(`/api/notebook/${p.id}`, { method: "PUT", body: { body: p.body } });
    p.updated_at = saved.updated_at;
    $("#page-meta").textContent = "Saved";
  } catch (e) { pageDirty = true; $("#page-meta").textContent = "Couldn't save: " + e.message; }
}

async function selectPage(id) {
  if (id === pages.current) return;
  await flushPage();
  const prev = pageById(pages.current);
  if (prev && isBlank(prev)) {                // don't leave blank notes lying around
    await api(`/api/notebook/${prev.id}`, { method: "DELETE" });
    pages.list = pages.list.filter((p) => p.id !== prev.id);
  }
  pages.current = id;
  renderPageList(); renderPageEditor();
}

async function loadPages() {
  pages.list = await api("/api/notebook" + (pages.q ? `?q=${encodeURIComponent(pages.q)}` : ""));
  if (!pageById(pages.current)) pages.current = pages.list.length ? pages.list[0].id : null;
  renderPageList(); renderPageEditor();
}

async function newPage(kind) {
  await flushPage();
  const cur = pageById(pages.current);
  if (cur && isBlank(cur)) {
    if (cur.kind === kind) { (isList(cur) ? $("#cl-title") : $("#page-body")).focus(); return; }  // reuse the blank one
    await api(`/api/notebook/${cur.id}`, { method: "DELETE" });
    pages.list = pages.list.filter((p) => p.id !== cur.id);
  }
  if (pages.q) { pages.q = ""; $("#notes-search").value = ""; await loadPages(); }
  const created = await api("/api/notebook", { method: "POST", body: { body: "", kind } });
  if (kind === "checklist") created.items = [await api(`/api/notebook/${created.id}/items`, { method: "POST", body: { text: "" } })];
  pages.list.unshift(created);
  pages.current = created.id;
  renderPageList(); renderPageEditor();
  (kind === "checklist" ? $("#cl-title") : $("#page-body")).focus();
}
$("#note-new").onclick = guard(() => newPage("text"));
$("#list-new").onclick = guard(() => newPage("checklist"));

/* --- text notes --- */
$("#page-body").addEventListener("input", () => {
  const p = pageById(pages.current);
  if (!p || isList(p)) return;
  p.body = $("#page-body").value;
  pageDirty = true;
  $("#page-meta").textContent = "Typing…";
  touch(p);
  clearTimeout(pageTimer);
  pageTimer = setTimeout(flushPage, 600);
});
$("#page-body").addEventListener("blur", flushPage);
window.addEventListener("beforeunload", () => { if (pageDirty || itemPending.size) flushPage(); });

$("#page-delete").onclick = guard(async () => {
  const p = pageById(pages.current);
  if (!p || (!isBlank(p) && !confirm(`Delete “${pageTitle(p)}”?`))) return;
  clearTimeout(pageTimer); pageDirty = false;
  (p.items || []).forEach((i) => dropPending(i.id));
  await api(`/api/notebook/${p.id}`, { method: "DELETE" });
  pages.list = pages.list.filter((x) => x.id !== p.id);
  pages.current = pages.list.length ? pages.list[0].id : null;
  renderPageList(); renderPageEditor(); toast("Note deleted");
});

let searchTimer;
$("#notes-search").addEventListener("input", (e) => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(guard(async () => { await flushPage(); pages.q = e.target.value.trim(); await loadPages(); }), 250);
});

/* --- checklists --- */
function updateProgress(p) {
  const total = p.items.length, done = doneCount(p);
  $("#cl-count").textContent = total ? (done === total ? `All ${total} done 🎉` : `${done} of ${total} done`) : "No items yet";
  $("#cl-bar").style.width = total ? (100 * done) / total + "%" : "0%";
}

function renderChecklistHeader(p) {
  $("#cl-mode").value = p.reset_mode;
  TimeField.set($("#cl-time"), p.reset_time);
  $("#cl-time").hidden = p.reset_mode !== "daily";
  $("#cl-last").textContent = "Last reset: " + (p.last_reset ? whenLabel(p.last_reset) : "never");
}

function renderChecklist(p) {
  $("#cl-title").value = p.body;
  renderChecklistHeader(p);
  $("#cl-items").replaceChildren(...p.items.map((it) => itemRow(p, it)));
  updateProgress(p);
}

function itemRow(p, it) {
  const row = el("div", { class: "cl-row" + (it.done ? " done" : ""), "data-id": it.id });
  const grip = el("span", { class: "grip", title: "Drag to reorder", "aria-hidden": "true" }, "⋮⋮");
  grip.addEventListener("mousedown", () => { row.draggable = true; });
  grip.addEventListener("mouseup", () => { row.draggable = false; });
  row.addEventListener("dragstart", (e) => { e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", String(it.id)); row.classList.add("dragging"); });
  row.addEventListener("dragend", () => { row.draggable = false; row.classList.remove("dragging"); persistOrder(p); });

  const box = el("input", { type: "checkbox", class: "cl-check", "aria-label": "Done" });
  box.checked = !!it.done;
  box.addEventListener("change", () => {
    it.done = box.checked ? 1 : 0;
    row.classList.toggle("done", box.checked);
    updateProgress(p); renderPageList();
    saveItem(it, { done: box.checked }, true);
  });

  const text = el("input", { class: "cl-text", placeholder: "To do", autocomplete: "off", "aria-label": "Item" });
  const comment = el("input", { class: "cl-comment", placeholder: "Comment", autocomplete: "off", "aria-label": "Comment" });
  text.value = it.text; comment.value = it.comment;
  for (const [input, field] of [[text, "text"], [comment, "comment"]]) {
    input.addEventListener("input", () => { it[field] = input.value; touch(p); saveItem(it, { [field]: input.value }); });
    input.addEventListener("change", () => saveItem(it, { [field]: input.value }, true));
  }
  text.addEventListener("keydown", guard(async (e) => {
    if (e.key === "Enter") { e.preventDefault(); if (text.value.trim()) await addItem(p, it.id); }
    else if (e.key === "Backspace" && !text.value && !comment.value) { e.preventDefault(); await removeItem(p, it, row); }
    else if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      const sib = e.key === "ArrowUp" ? row.previousElementSibling : row.nextElementSibling;
      if (sib) { e.preventDefault(); $(".cl-text", sib).focus(); }
    }
  }));
  comment.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); comment.blur(); } });

  const del = el("button", { class: "icon-btn x", title: "Delete item", "aria-label": "Delete item", type: "button", onclick: guard(() => removeItem(p, it, row)) }, "✕");
  row.append(grip, box, text, comment, del);
  return row;
}

async function addItem(p, afterId) {
  const it = await api(`/api/notebook/${p.id}/items`, { method: "POST", body: { text: "", after_id: afterId ?? null } });
  const idx = afterId ? p.items.findIndex((i) => i.id === afterId) + 1 : p.items.length;
  p.items.splice(idx, 0, it);
  const row = itemRow(p, it), after = afterId ? $(`#cl-items [data-id="${afterId}"]`) : null;
  if (after) after.after(row); else $("#cl-items").append(row);
  updateProgress(p); renderPageList();
  $(".cl-text", row).focus();
}

async function removeItem(p, it, row) {
  dropPending(it.id);
  const target = row.previousElementSibling || row.nextElementSibling;
  await api(`/api/notebook/items/${it.id}`, { method: "DELETE" });
  p.items = p.items.filter((i) => i.id !== it.id);
  row.remove();
  updateProgress(p); renderPageList();
  if (target) $(".cl-text", target).focus();
}

// drag & drop reordering (the container listener is attached once)
$("#cl-items").addEventListener("dragover", (e) => {
  const dragging = $("#cl-items .dragging");
  if (!dragging) return;
  e.preventDefault();
  const after = [...document.querySelectorAll("#cl-items .cl-row:not(.dragging)")]
    .find((r) => e.clientY < r.getBoundingClientRect().top + r.offsetHeight / 2);
  $("#cl-items").insertBefore(dragging, after || null);
});
$("#cl-items").addEventListener("drop", (e) => e.preventDefault());
async function persistOrder(p) {
  const ids = [...document.querySelectorAll("#cl-items .cl-row")].map((r) => Number(r.dataset.id));
  if (ids.join() === p.items.map((i) => i.id).join()) return;
  p.items = ids.map((id) => p.items.find((i) => i.id === id));
  try { await api(`/api/notebook/${p.id}/order`, { method: "PUT", body: { ids } }); }
  catch (e) { toast("Couldn't save order: " + e.message); }
}

$("#cl-title").addEventListener("input", () => {
  const p = pageById(pages.current);
  if (!isList(p)) return;
  p.body = $("#cl-title").value;
  pageDirty = true; touch(p);
  clearTimeout(pageTimer); pageTimer = setTimeout(flushPage, 600);
});
$("#cl-title").addEventListener("blur", flushPage);
$("#cl-title").addEventListener("keydown", guard(async (e) => {
  if (e.key !== "Enter") return;
  e.preventDefault();
  const p = pageById(pages.current), first = $("#cl-items .cl-text");
  if (first) first.focus(); else await addItem(p, null);
}));
$("#cl-add").onclick = guard(async () => {
  const p = pageById(pages.current);
  if (isList(p)) await addItem(p, p.items.length ? p.items[p.items.length - 1].id : null);
});

async function saveChecklistSettings(mode, time) {
  const p = pageById(pages.current);
  if (!isList(p)) return;
  const saved = await api(`/api/notebook/${p.id}/settings`, { method: "PUT", body: { reset_mode: mode, reset_time: time } });
  Object.assign(p, { reset_mode: saved.reset_mode, reset_time: saved.reset_time, last_reset: saved.last_reset });
  renderChecklistHeader(p); renderPageList();
}
$("#cl-mode").onchange = guard(async () => {
  const p = pageById(pages.current);
  await saveChecklistSettings($("#cl-mode").value, TimeField.get($("#cl-time")) || p.reset_time);
  if ($("#cl-mode").value === "daily") $("#cl-time").focus();
});
$("#cl-time").addEventListener("blur", guard(async () => {   // runs after TimeField's own blur has tidied the value
  const p = pageById(pages.current), v = TimeField.get($("#cl-time"));
  if (!isList(p)) return;
  if (!v) { TimeField.set($("#cl-time"), p.reset_time); return; }
  if (v !== p.reset_time) { await saveChecklistSettings(p.reset_mode, v); toast(`Resets daily at ${v}`); }
}));
$("#cl-time").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); $("#cl-time").blur(); } });
$("#cl-reset-now").onclick = guard(async () => {
  const p = pageById(pages.current);
  if (!isList(p)) return;
  await flushItems();
  const saved = await api(`/api/notebook/${p.id}/reset`, { method: "POST" });
  p.items = saved.items; p.last_reset = saved.last_reset;
  renderChecklist(p); renderPageList(); toast("Checklist reset");
});

// pick up a scheduled (daily) reset while the tab is open, without disturbing anything being edited
let notesActivity = 0;   // last time the user touched anything in the notes editor
for (const type of ["input", "change", "click", "keydown"]) {
  $("#notes-editor").addEventListener(type, () => { notesActivity = Date.now(); }, true);
}
async function refreshPagesQuietly() {
  if (!$("#tab-notes").classList.contains("active") || pageDirty || itemPending.size) return;
  if (Date.now() - notesActivity < 8000) return;   // saves may still be in flight; try again next minute
  const a = document.activeElement;
  if (a && a.closest && a.closest("#notes-editor") && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) return;
  await loadPages();
}
setInterval(guard(refreshPagesQuietly), 60_000);

/* ---------- chat ---------- */
let chatReady = false, chatBusy = false;

function escapeHtml(s) { return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function inline(s) {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
}
function markdown(src) {
  const out = []; let list = null, code = null;
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
  for (const line of src.split("\n")) {
    if (line.startsWith("```")) {
      if (code === null) { closeList(); code = []; } else { out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`); code = null; }
      continue;
    }
    if (code !== null) { code.push(line); continue; }
    const ul = line.match(/^\s*[-*] (.*)/), ol = line.match(/^\s*\d+\. (.*)/);
    if (ul || ol) {
      const kind = ul ? "ul" : "ol";
      if (list !== kind) { closeList(); out.push(`<${kind}>`); list = kind; }
      out.push(`<li>${inline((ul || ol)[1])}</li>`);
    } else {
      closeList();
      if (line.trim()) out.push(`<p>${inline(line)}</p>`);
    }
  }
  if (code !== null) out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  closeList();
  return out.join("");
}

function addMsg(role, text) {
  const m = el("div", { class: "msg " + role });
  if (role === "assistant") m.innerHTML = markdown(text); else m.textContent = text;
  $("#chat-log").append(m);
  scrollChat();
  return m;
}
const scrollChat = () => { const l = $("#chat-log"); l.scrollTop = l.scrollHeight; };

async function initChat() {
  if (chatReady) return;
  const [{ configured }, history] = await Promise.all([api("/api/chat/status"), api("/api/chat/history")]);
  $("#chat-setup").hidden = configured;
  $("#chat-send").disabled = !configured;
  history.forEach((m) => addMsg(m.role, m.text));
  if (!history.length && configured) addMsg("assistant", "Hi! I can add reminders and events, write notes on dates, and tell you what's coming up. Try: **remind me tomorrow at 9 to submit my CA**.");
  chatReady = true;
}

$("#chat-form").addEventListener("submit", guard(async (e) => {
  e.preventDefault();
  if (chatBusy) return;
  const input = $("#chat-input"), text = input.value.trim();
  if (!text) return;
  input.value = ""; input.style.height = "auto";
  addMsg("user", text);
  chatBusy = true; $("#chat-send").disabled = true;
  let bubble = null, raw = "", acted = false;
  try {
    const res = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: text }) });
    if (!res.ok) throw new Error(`Server error ${res.status}`);
    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        if (!frame.startsWith("data: ")) continue;
        const ev = JSON.parse(frame.slice(6));
        if (ev.type === "text") {
          if (!bubble) { bubble = addMsg("assistant", ""); raw = ""; }
          raw += ev.text; bubble.innerHTML = markdown(raw); scrollChat();
        } else if (ev.type === "tool") {
          bubble = null; acted = acted || ev.ok;
          $("#chat-log").append(el("div", { class: "tool-chip" + (ev.ok ? "" : " bad") }, (ev.ok ? "✓ " : "⚠ ") + ev.label));
          scrollChat();
        } else if (ev.type === "error") {
          addMsg("error", ev.message);
          if (ev.code === "no_key" || ev.code === "bad_key") $("#chat-setup").hidden = false;
        }
      }
    }
  } finally {
    chatBusy = false; $("#chat-send").disabled = false; input.focus();
    if (acted) await loadAll();
  }
}));
$("#chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#chat-form").requestSubmit(); }
});
$("#chat-input").addEventListener("input", (e) => { e.target.style.height = "auto"; e.target.style.height = Math.min(e.target.scrollHeight, 160) + "px"; });
$("#chat-clear").onclick = guard(async () => {
  if (!confirm("Clear the whole conversation?")) return;
  await api("/api/chat/history", { method: "DELETE" });
  $("#chat-log").replaceChildren(); chatReady = false; await initChat();
});

/* ---------- settings ---------- */
const setDlg = $("#settings-dialog");
$("#open-settings").onclick = guard(async () => {
  const s = await api("/api/settings");
  $("#set-topic").textContent = s.ntfy_topic;
  $("#st-claude").classList.toggle("on", s.claude_configured);
  $("#st-login").classList.toggle("on", s.launch_at_login);
  $("#test-status").textContent = "";
  setDlg.showModal();
});
$("#settings-close").onclick = () => setDlg.close();
$("#copy-topic").onclick = guard(async () => { await navigator.clipboard.writeText($("#set-topic").textContent); toast("Topic copied"); });
$("#test-notif").onclick = guard(async () => {
  $("#test-status").textContent = "Sending…";
  const { ok } = await api("/api/settings/test-notification", { method: "POST" });
  $("#test-status").textContent = ok ? "Sent — check your phone." : "Failed — are you online?";
});

/* ---------- progress (checklist history heatmap) ---------- */
let progressPick = null;
async function loadProgress() {
  const data = await api("/api/progress" + (progressPick ? `?note_id=${progressPick}` : ""));
  const sel = $("#pg-list");
  $("#pg-empty").hidden = data.checklists.length > 0;
  $("#pg-body").hidden = data.checklists.length === 0;
  sel.hidden = data.checklists.length < 2;
  sel.replaceChildren(...data.checklists.map((c) => el("option", { value: c.id, selected: c.id === data.selected }, c.title)));
  progressPick = data.selected;
  if (!data.selected) return;

  // weeks as columns, Monday at the top, ending with the current week
  const end = new Date(); end.setHours(0, 0, 0, 0);
  // begin at the week of the first logged day (or this week), so it grows from today instead of showing a blank year
  const first = Object.keys(data.days).sort()[0];
  const start = first ? parseDay(first) : new Date(end);
  start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
  const cap = new Date(end); cap.setDate(cap.getDate() - ((cap.getDay() + 6) % 7) - 52 * 7);
  if (start < cap) start.setTime(cap.getTime());
  const grid = $("#pg-grid"), months = $("#pg-months"), tip = $("#pg-tip");
  grid.replaceChildren(); months.replaceChildren();
  let lastMonth = -1, col = 0;
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    const key = ymd(d), info = data.days[key];
    if (d.getDay() === 1) {
      col++;
      if (d.getMonth() !== lastMonth && (d.getDate() <= 7 || col === 1)) {
        months.append(el("span", { style: `grid-column:${col}` }, MONTHS[d.getMonth()].slice(0, 3)));
      }
      lastMonth = d.getMonth();
    }
    const frac = info ? info.done.length / info.total : -1;
    const level = frac < 0 ? "" : frac === 0 ? "l0" : frac <= 0.25 ? "l1" : frac <= 0.5 ? "l2" : frac < 1 ? "l3" : "l4";
    const cell = el("i", { class: `hm ${level}${key === today() ? " today" : ""}` });
    const label = parseDay(key).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
    cell.addEventListener("mouseenter", () => {
      tip.replaceChildren(
        el("strong", {}, label),
        el("div", { class: "muted" }, info ? `${info.done.length} of ${info.total} done` : "No data"),
        ...(info ? info.done.map((t) => el("div", {}, "✓ " + t)) : []));
      tip.hidden = false;
      const r = cell.getBoundingClientRect(), t = tip.getBoundingClientRect();
      tip.style.left = Math.max(8, Math.min(innerWidth - t.width - 8, r.left + r.width / 2 - t.width / 2)) + "px";
      tip.style.top = (r.top - t.height - 8 < 8 ? r.bottom + 8 : r.top - t.height - 8) + "px";
    });
    cell.addEventListener("mouseleave", () => (tip.hidden = true));
    grid.append(cell);
  }
  const total = Object.keys(data.days).length;
  $("#pg-note").textContent = total
    ? `${total} day${total === 1 ? "" : "s"} logged. A day is saved each time the checklist resets.`
    : "Nothing logged yet — a day is saved each time the checklist resets, so the grid fills in from here.";
}
$("#pg-list").addEventListener("change", (e) => { progressPick = Number(e.target.value); guard(loadProgress)(); });

/* ---------- boot ---------- */
guard(loadAll)();
function tabFromHash() {
  const name = location.hash.slice(1);
  if (name && $(`#tab-${name}`)) showTab(name);
}
tabFromHash();
window.addEventListener("hashchange", tabFromHash);
// keep overdue badge + calendar fresh, but never clobber a note or dialog being edited
setInterval(() => {
  if (noteDirtyFor !== null || document.activeElement === $("#day-note") || evDlg.open || setDlg.open) return;
  guard(loadAll)();
}, 60_000);
