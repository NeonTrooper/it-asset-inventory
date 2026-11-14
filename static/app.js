/* ============================================================
   app.js — the page of the IT asset inventory.

   The page holds no data of its own. Everything comes from the
   server's JSON API (see app.py) using fetch():

       load()  ->  GET /api/assets + /api/summary  ->  draw the table
       save    ->  POST or PUT /api/assets          ->  load() again
   ============================================================ */
const TYPES = ["laptop", "desktop", "monitor", "phone", "printer", "network", "other"];
const STATUSES = { in_stock: "In stock", assigned: "Assigned", repair: "In repair", retired: "Retired" };
const WARN_DAYS = 60;

const $ = (id) => document.getElementById(id);
let editingId = null;          // null = the form is adding a new asset

/* Create an element; text is set with textContent so data from the
   database can never be interpreted as HTML. */
function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  children.forEach((c) => c && node.append(c));
  return node;
}

/* Call the API. Returns { ok, data } so callers can show the server's error messages. */
async function api(method, url, body) {
  const response = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  return { ok: response.ok, data: await response.json() };
}

/* "ok", "soon" (within 60 days), "expired" or "" when there is no date. */
function warrantyState(asset) {
  if (!asset.warranty_end || asset.status === "retired") return "";
  const days = (new Date(asset.warranty_end) - new Date(new Date().toDateString())) / 86400000;
  return days < 0 ? "expired" : days <= WARN_DAYS ? "soon" : "ok";
}

/* ---------- drawing ---------- */
function drawTiles(s) {
  const tile = (label, value, cls = "") => el("div", { class: cls }, [el("dt", { text: label }), el("dd", { text: value })]);
  $("tiles").replaceChildren(
    tile("Total assets", s.total),
    tile("Assigned", s.by_status.assigned),
    tile("In stock", s.by_status.in_stock),
    tile("In repair", s.by_status.repair),
    tile(`Warranty ends within ${s.warn_days} days`, s.warranty_expiring, s.warranty_expiring ? "warn" : ""),
    tile("Warranty expired", s.warranty_expired, s.warranty_expired ? "bad" : ""));
}

function drawRows(assets) {
  $("rows").replaceChildren(...assets.map((a) => {
    const state = warrantyState(a);
    const note = { soon: " (ending soon)", expired: " (expired)" }[state] || "";
    return el("tr", {}, [
      el("td", { class: "tag", text: a.tag }),
      el("td", { text: a.type }),
      el("td", {}, [el("span", { text: a.model }), a.serial ? el("small", { text: "S/N " + a.serial }) : null]),
      el("td", {}, [el("span", { class: "pill " + a.status, text: STATUSES[a.status] })]),
      el("td", { text: a.assigned_to || "–" }),
      el("td", { text: a.location || "–" }),
      el("td", { class: "warranty " + state, text: a.warranty_end ? a.warranty_end + note : "–" }),
      el("td", { class: "row-actions" }, [
        el("button", { type: "button", text: "Edit", onclick: () => openForm(a) }),
        el("button", { type: "button", text: "History", onclick: () => openHistory(a) }),
        el("button", { type: "button", class: "danger", text: "Delete", onclick: (e) => remove(a, e.currentTarget) }),
      ]),
    ]);
  }));
  $("empty").hidden = assets.length > 0;
}

async function load() {
  const params = new URLSearchParams({ q: $("q").value, status: $("f-status").value, type: $("f-type").value });
  const [assets, summary] = await Promise.all([api("GET", "/api/assets?" + params), api("GET", "/api/summary")]);
  drawRows(assets.data);
  drawTiles(summary.data);
}

/* ---------- add / edit ---------- */
function openForm(asset) {
  editingId = asset ? asset.id : null;
  $("dialog-title").textContent = asset ? "Edit " + asset.tag : "Add asset";
  $("errors").textContent = "";
  const form = $("form");
  form.reset();
  if (asset) for (const field of form.elements) if (field.name) field.value = asset[field.name] ?? "";
  $("dialog").showModal();
}

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const body = Object.fromEntries(new FormData(event.target));
  const result = editingId === null
    ? await api("POST", "/api/assets", body)
    : await api("PUT", "/api/assets/" + editingId, body);
  if (!result.ok) {                       // the server validates; show what it said
    $("errors").textContent = result.data.errors.join(" ");
    return;
  }
  $("dialog").close();
  load();
});

async function remove(asset, button) {
  // A second click is required, so one stray click cannot delete a device.
  if (remove.pending !== asset.id) {
    remove.pending = asset.id;
    button.textContent = "Click again to delete";
    setTimeout(() => { remove.pending = null; load(); }, 3000);
    return;
  }
  remove.pending = null;
  await api("DELETE", "/api/assets/" + asset.id);
  load();
}

/* ---------- history ---------- */
async function openHistory(asset) {
  const { data } = await api("GET", `/api/assets/${asset.id}/history`);
  $("history-title").textContent = "History of " + asset.tag;
  $("history-list").replaceChildren(...data.map((h) =>
    el("li", {}, [el("time", { text: h.at.replace("T", " ") }), el("span", { text: h.event })])));
  $("history").showModal();
}

/* ---------- setup ---------- */
function fill(id, entries, placeholder) {
  $(id).replaceChildren(
    ...(placeholder ? [el("option", { value: "", text: placeholder })] : []),
    ...entries.map(([value, label]) => el("option", { value, text: label })));
}
fill("type", TYPES.map((t) => [t, t]));
fill("status", Object.entries(STATUSES));
fill("f-type", TYPES.map((t) => [t, t]), "All types");
fill("f-status", Object.entries(STATUSES), "All statuses");

$("add").addEventListener("click", () => openForm(null));
$("cancel").addEventListener("click", () => $("dialog").close());
$("history-close").addEventListener("click", () => $("history").close());
$("f-status").addEventListener("change", load);
$("f-type").addEventListener("change", load);
let timer;                                 // wait until typing pauses before searching
$("q").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(load, 200); });

load();
