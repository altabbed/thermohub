"use strict";

const STORES = [
  { id: "woolworths", name: "Woolworths" },
  { id: "coles", name: "Coles" },
];
const SERVICES = [{ id: "cookidoo", name: "Cookidoo", logo: "pot" }, ...STORES.map((s) => ({ ...s, logo: "cart" }))];
const KEY_STORAGE = "thermohub-api-key";
const money = new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD" });

// ---- Small helpers ----------------------------------------------------------

const ICONS = {
  cart: '<path d="M3 4h2.2l2.3 10.3a2 2 0 0 0 2 1.7h7.1a2 2 0 0 0 1.9-1.4L21 8H6.1"/><circle cx="10" cy="20" r="1.2"/><circle cx="17" cy="20" r="1.2"/>',
  pot: '<path d="M4 10h16v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M2 10h20"/><path d="M9 6.5C9 5 10.3 4 12 4s3 1 3 2.5"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  back: '<path d="M15 18l-6-6 6-6"/>',
  chev: '<path d="M6 9l6 6 6-6"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  tag: '<path d="M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.3"/>',
  bulb: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/>',
  lock: '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  star: '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"/>',
  warn: '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17.5v.01"/>',
  minus: '<path d="M7 12h10"/>',
  plus: '<path d="M12 7v10M7 12h10"/>',
  circle: '<circle cx="12" cy="12" r="7"/>',
  open: '<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name]; // fixed markup only, never user data
  return svg;
}

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "style") node.style.cssText = value;
    else if (key === "onclick") node.addEventListener("click", value);
    else if (key.startsWith("data-") || key.startsWith("aria-") || key === "for" || key === "role") node.setAttribute(key, value);
    else node[key] = value;
  }
  node.append(...clean(children));
  return node;
}

function clean(children) {
  return children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false);
}

// Like replaceChildren, but skips empty parts so optional pieces can be written inline.
function fill(node, ...children) {
  node.replaceChildren(...clean(children));
}

const nice = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : text);
const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;
const safeUrl = (value) => (typeof value === "string" && /^https?:\/\//.test(value) ? value : null);

function listOf(words) {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

function photo(url) {
  const blank = () => el("span", { class: "noimg" });
  if (!safeUrl(url) && !/^data:image\//.test(url || "")) return blank();
  // No referrer: the shops' image servers needn't see Thermohub's address.
  const img = el("img", { referrerPolicy: "no-referrer", loading: "lazy", alt: "", src: url });
  img.addEventListener("error", () => img.replaceWith(blank()));
  return img;
}

function details(text) {
  return text ? el("details", { class: "details" }, el("summary", {}, "Details"), el("p", {}, text)) : null;
}

// ---- Talking to Thermohub -----------------------------------------------------

class NeedsKey extends Error {}
class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

let accessKey = "";
try { accessKey = localStorage.getItem(KEY_STORAGE) || ""; } catch { /* storage blocked */ }

async function api(path, options = {}) {
  const headers = options.body ? { "Content-Type": "application/json" } : {};
  if (accessKey) headers["X-API-Key"] = accessKey;
  let res;
  try {
    res = await fetch(`api/${path}`, { ...options, headers });
  } catch {
    throw new ApiError("Thermohub isn't answering. Check that the server is switched on, then try again.", 0);
  }
  if (res.status === 401) throw new NeedsKey();
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new ApiError(typeof body.detail === "string" ? body.detail : "Something went wrong. Please try again.", res.status);
  }
  return body;
}

const data = {
  status: null,
  statusAt: 0,
  statusLoading: null,
  list: null,
  seen: new Set(),
  selected: new Set(),
  comparison: null,
  comparing: null, // the comparison running on Thermohub: {id, total, checked}
  compareError: null,
  compared: [], // the shopping list items behind each comparison row
  picks: [], // per item, per shop: which candidate is chosen
  qty: [], // per item, per shop: how many of it, once changed here
  amount: [], // per item: the amount priced at both shops, {kind, value}
  chosen: [], // per item: the shops where a product was picked by hand
  showAll: new Set(), // items whose full list of options is open
  plan: null, // "woolworths", "coles" or "split"
  storeFor: [], // per item: a shop chosen by hand, overriding the plan
  open: new Set(),
  account: null,
  regions: null,
  accounts: null, // shop sign-ins
  returnTo: "accounts", // where the shop sign-in screen goes afterwards
  cartRequest: null,
  cartJob: null,
  cartError: null,
};

// Called when leaving a screen that holds something open (the live sign-in).
let leaveScreen = null;

function loadStatus(force = false) {
  if (!force && data.status && Date.now() - data.statusAt < 60_000) return Promise.resolve(data.status);
  if (!force && data.statusLoading) return data.statusLoading;
  data.statusLoading = api("status")
    .then((status) => {
      data.status = status;
      data.statusAt = Date.now();
      return status;
    })
    .finally(() => (data.statusLoading = null));
  return data.statusLoading;
}

async function loadList() {
  const list = await api("shopping-list");
  // Everything is ticked to start with; anything new on the list arrives ticked too.
  const names = list.items.map((i) => i.name);
  data.selected = new Set(names.filter((n) => data.selected.has(n) || !data.seen.has(n)));
  data.seen = new Set(names);
  data.list = list;
  return list;
}

async function loadAccount() {
  const [account, regions] = await Promise.all([api("cookidoo/account"), data.regions || api("cookidoo/regions")]);
  data.account = account;
  data.regions = regions;
  return account;
}

function loadAccounts(force = false) {
  if (!force && data.accounts) return Promise.resolve(data.accounts);
  return api("stores").then((accounts) => (data.accounts = accounts));
}

const signedIn = (storeId) => Boolean(data.accounts && data.accounts.some((a) => a.id === storeId && a.signed_in));
const storeById = (id) => STORES.find((s) => s.id === id);
const storeDot = (id) => el("span", { class: "store-dot", style: `--brand: var(--${id})` });

function failed(err) {
  if (err instanceof NeedsKey) showKeyScreen();
  else console.error(err);
}

// ---- Screens ------------------------------------------------------------------

const SCREENS = { list: showList, compare: showCompare, accounts: showSettings, signin: showSignIn, cart: showCart, check: showCheck };

// "#signin/coles" is the sign-in screen for Coles.
function route() {
  const [name, arg] = location.hash.slice(1).split("/");
  return SCREENS[name] ? { name, arg } : { name: "list" };
}

const screenName = () => route().name;

function go(hash) {
  if (location.hash.slice(1) === hash) render();
  else location.hash = hash;
}

function render() {
  if (leaveScreen) leaveScreen();
  leaveScreen = null;
  hideAction();
  fill(app());
  window.scrollTo(0, 0);
  const { name, arg } = route();
  SCREENS[name](app(), arg);
}

const app = () => document.getElementById("app");

function header(back = false) {
  if (back) {
    const to = back === true ? { href: "#list", label: "Shopping list" } : back;
    return el("header", { class: "header" },
      el("a", { class: "icon-button back", href: to.href }, icon("back"), to.label));
  }
  return el("header", { class: "header" },
    el("a", { class: "brand", href: "#list" }, el("span", { class: "brand-mark" }, icon("cart")), "Thermohub"),
    el("a", { class: "icon-button", href: "#accounts" }, icon("gear"), "Settings"));
}

// Three small chips: is each service working? Tapping one opens Settings.
const SHORT_STATE = {
  connected: null,
  not_set_up: "not connected",
  needs_attention: "sign in again",
  unavailable: "not responding",
};

function chips() {
  const box = el("nav", { class: "chips", "aria-label": "Connections" });
  const draw = (status) => fill(box, SERVICES.map((s) => {
    const c = status && status.connections.find((x) => x.id === s.id);
    const kind = !c ? "checking" : c.state === "connected" ? "ok" : c.state === "not_set_up" ? "off" : "problem";
    const words = c ? SHORT_STATE[c.state] : "checking";
    return el("a", {
      class: `chip ${kind}`,
      href: "#accounts",
      title: c ? c.summary : "Checking…",
      "aria-label": `${s.name}: ${words || "working"}`,
    }, el("span", { class: "dot" }), s.name, words ? ` · ${words}` : null);
  }));
  draw(data.status);
  loadStatus().then(draw).catch(failed);
  return box;
}

// -- Shopping list (home) --

function showList(root) {
  const body = el("div", { class: "stack", style: "gap: 16px" });
  root.append(header(), chips(), body);
  if (data.list) drawList(body);
  else fill(body, el("div", { class: "title" }, el("h1", {}, "Shopping list"), el("p", {}, "Getting your list from Cookidoo…")),
    el("div", { class: "card skeleton" }, el("div"), el("div"), el("div"), el("div")));
  // Fetch it fresh, but only redraw when it changed, so the screen doesn't jump under a finger.
  const shown = data.list && JSON.stringify(data.list.items);
  loadList()
    .then(() => body.isConnected && JSON.stringify(data.list.items) !== shown && drawList(body))
    .catch((err) => body.isConnected && listProblem(body, err));
}

function drawList(body) {
  const { items, recipes } = data.list;
  if (!items.length) {
    hideAction();
    fill(body, el("div", { class: "card empty" },
      el("strong", {}, "Your shopping list is empty"),
      el("p", {}, "Add recipes to your shopping list in Cookidoo or on your Thermomix, and they'll appear here."),
      el("button", { class: "button secondary small", type: "button", onclick: render }, "Check again")));
    return;
  }
  const toggle = el("button", { class: "text-button", type: "button" });
  const label = () => (toggle.textContent = data.selected.size === items.length ? "Untick all" : "Tick all");
  toggle.addEventListener("click", () => {
    data.selected = data.selected.size === items.length ? new Set() : new Set(items.map((i) => i.name));
    drawList(body);
  });
  label();
  const rows = items.map((item, index) => {
    const id = `item-${index}`;
    const box = el("input", { type: "checkbox", class: "tick", id, checked: data.selected.has(item.name) });
    const row = el("label", { class: `pick-row${data.selected.has(item.name) ? "" : " off"}`, for: id },
      box,
      el("span", { class: "name" }, nice(item.name)),
      el("span", { class: "amount" }, item.display),
      el("span", { class: "for" }, forText(item)));
    box.addEventListener("change", () => {
      if (box.checked) data.selected.add(item.name);
      else data.selected.delete(item.name);
      row.classList.toggle("off", !box.checked);
      label();
      compareAction();
    });
    return el("li", {}, row);
  });
  fill(body,
    el("div", { class: "row-between" },
      el("div", { class: "title" }, el("h1", {}, "Shopping list"),
        el("p", {}, `${plural(items.length, "item")} for ${plural(recipes.length, "recipe")}`)),
      toggle),
    el("ul", { class: "card rows" }, rows));
  compareAction();
}

function forText(item) {
  const recipes = [...new Set(item.sources.map((s) => s.recipe_name).filter(Boolean))];
  const added = item.sources.some((s) => s.type === "additional");
  if (!recipes.length) return "Added by you";
  return `For ${listOf(recipes)}${added ? ", and added by you" : ""}`;
}

function compareAction() {
  const n = data.selected.size;
  const label = n ? `Compare prices for ${plural(n, "item")}` : "Tick items to compare";
  // Update the button in place: replacing it under a finger can lose the tap.
  const bar = document.getElementById("action");
  const current = !bar.hidden && bar.querySelector("button[data-action=compare]");
  if (current) {
    current.textContent = label;
    current.disabled = n === 0;
    return;
  }
  showAction(el("button", { class: "button primary wide", type: "button", "data-action": "compare", disabled: n === 0, onclick: startComparison }, label));
}

async function listProblem(body, err) {
  hideAction();
  if (err instanceof NeedsKey) return showKeyScreen();
  if (err.status !== 409) {
    fill(body, el("div", { class: "card message" },
      el("h2", {}, "Can't get your shopping list right now"),
      el("p", {}, err.message),
      el("div", {}, el("button", { class: "button secondary small", type: "button", onclick: render }, "Try again"))));
    return;
  }
  fill(body, el("div", { class: "card working" }, el("span", { class: "spinner" }), "One moment…"));
  try {
    await loadAccount();
  } catch (e) {
    return failed(e);
  }
  const again = Boolean(data.account.email);
  fill(body,
    el("div", { class: "title" },
      el("h1", {}, again ? "Cookidoo signed you out" : "Let's bring in your shopping list"),
      el("p", {}, again
        ? "Sign in again to see your shopping list."
        : "Connect the Cookidoo account you use with your Thermomix. You only need to do this once.")),
    el("div", { class: "card" }, connectForm(() => {
      data.status = null;
      render();
    })));
}

// -- Cookidoo sign-in form (home on first visit, and Settings) --

function regionLabel(region) {
  let country = region.country.toUpperCase();
  let language = region.language;
  try { country = new Intl.DisplayNames(["en"], { type: "region" }).of(country) || country; } catch { /* keep code */ }
  try { language = new Intl.DisplayNames(["en"], { type: "language" }).of(language) || language; } catch { /* keep code */ }
  return `${country} (${language})`;
}

function connectForm(onConnected) {
  const { account, regions } = data;
  const options = regions
    .map((r) => ({ value: `${r.country}|${r.language}`, label: regionLabel(r) }))
    .sort((a, b) => a.label.localeCompare(b.label));
  const current = `${account.country}|${account.language}`;
  const currentLabel = (options.find((o) => o.value === current) || options[0]).label;

  const country = el("select", { id: "cookidoo-country" },
    options.map((o) => el("option", { value: o.value, selected: o.value === current }, o.label)));
  const countryField = el("label", { class: "field", for: "cookidoo-country", hidden: true }, "Country", country);
  const countryLine = el("p", { class: "note" }, "Country: ", el("strong", {}, currentLabel.split(" (")[0]), " · ",
    el("button", { class: "text-button", type: "button", onclick: () => { countryLine.hidden = true; countryField.hidden = false; } }, "Change"));

  const email = el("input", { id: "cookidoo-email", type: "email", autocomplete: "username", required: true, value: account.email || "" });
  const password = el("input", { id: "cookidoo-password", type: "password", autocomplete: "current-password", required: true });
  const show = el("button", { class: "button secondary small", type: "button" }, "Show");
  show.addEventListener("click", () => {
    const hidden = password.type === "password";
    password.type = hidden ? "text" : "password";
    show.textContent = hidden ? "Hide" : "Show";
  });
  const error = el("p", { class: "form-error", role: "alert", hidden: true });
  const submit = el("button", { class: "button primary wide", type: "submit" }, "Connect Cookidoo");

  const form = el("form", { class: "form" },
    el("label", { class: "field", for: "cookidoo-email" }, "Email", email),
    el("label", { class: "field", for: "cookidoo-password" }, "Password", el("div", { class: "password" }, password, show)),
    countryLine,
    countryField,
    error,
    submit,
    el("p", { class: "note" }, "Use the email and password you use for Cookidoo. Your password is only used to sign in and isn't saved."));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const [c, language] = country.value.split("|");
    error.hidden = true;
    submit.disabled = true;
    submit.textContent = "Connecting…";
    try {
      data.account = await api("cookidoo/sign-in", {
        method: "POST",
        body: JSON.stringify({ email: email.value.trim(), password: password.value, country: c, language }),
      });
      onConnected();
    } catch (err) {
      if (err instanceof NeedsKey) return showKeyScreen();
      error.textContent = err.message;
      error.hidden = false;
      submit.disabled = false;
      submit.textContent = "Connect Cookidoo";
    }
  });
  // Jump straight into the form on computers; on phones that would pop the keyboard up unasked.
  if (!account.email && matchMedia("(pointer: fine)").matches) setTimeout(() => email.isConnected && email.focus({ preventScroll: true }), 0);
  return form;
}

// -- Price comparison --

// A comparison runs on Thermohub, which can take a minute or two for a long
// list. The page asks how it's going every second, so leaving the page,
// locking the phone or a reload doesn't lose it.
const COMPARISON = "thermohub-comparison";
const savedComparison = {
  get() { try { return localStorage.getItem(COMPARISON); } catch { return null; } },
  set(id) { try { localStorage.setItem(COMPARISON, id); } catch { /* storage blocked */ } },
  clear() { try { localStorage.removeItem(COMPARISON); } catch { /* storage blocked */ } },
};

async function startComparison() {
  if (!data.list) return go("list");
  const items = data.list.items.filter((i) => data.selected.has(i.name));
  clearTimeout(data.compareTimer);
  data.comparison = null;
  data.compareError = null;
  data.open = new Set();
  data.compared = items;
  data.comparing = { id: null, done: false, total: items.length, checked: {} };
  go("compare");
  try {
    data.comparing = await api("comparisons", {
      method: "POST",
      body: JSON.stringify({
        items: items.map((i) => ({
          name: i.name,
          key: i.ingredient_ref ? `ref:${i.ingredient_ref}` : null,
          quantities: i.quantities,
        })),
      }),
    });
    savedComparison.set(data.comparing.id);
    followComparison(500);
  } catch (err) {
    data.comparing = null;
    data.compareError = err;
    if (screenName() === "compare") render();
  }
}

function followComparison(wait = 1000) {
  clearTimeout(data.compareTimer);
  data.compareTimer = setTimeout(async () => {
    const id = data.comparing && data.comparing.id;
    if (!id) return;
    let run;
    try {
      run = await api(`comparisons/${id}`);
    } catch (err) {
      if (err instanceof NeedsKey) return showKeyScreen();
      if (err.status !== 404) return followComparison(3000); // Thermohub blinked: keep asking
      savedComparison.clear();
      data.comparing = null;
      data.compareError = err;
      if (screenName() === "compare") render();
      return;
    }
    if (!data.comparing || data.comparing.id !== id) return; // a newer comparison took over
    if (!run.done) {
      data.comparing = run;
      updateSplash();
      return followComparison();
    }
    data.comparing = null;
    if (run.error) data.compareError = new ApiError(run.error, 500);
    else useComparison(run.result);
    if (screenName() === "compare") render();
  }, wait);
}

function useComparison(comparison) {
  data.comparison = comparison;
  // After a reload the shopping list may be gone from memory: match by name.
  if (data.compared.length !== comparison.items.length) {
    data.compared = comparison.items.map((item) => data.list && data.list.items.find((x) => x.name === item.name));
  }
  data.picks = comparison.items.map(() => ({}));
  data.qty = comparison.items.map(() => ({}));
  data.amount = comparison.items.map((item) => item.amount);
  data.chosen = comparison.items.map(() => new Set());
  data.showAll = new Set();
  data.storeFor = comparison.items.map(() => null);
  data.plan = defaultPlan(comparison);
}

// Start with the cheaper shop; if neither is cheaper, the one that has more of the items.
function defaultPlan(comparison) {
  if (comparison.cheapest) return comparison.cheapest;
  const most = [...comparison.totals].sort((a, b) => b.found - a.found)[0];
  return most ? most.store : STORES[0].id;
}

function showCompare(root) {
  const body = el("div", { class: "stack", style: "gap: 18px" });
  root.append(header(true), body);
  if (!data.comparing && !data.comparison && !data.compareError) {
    // Opened afresh (a reload, or back from another app): pick up the last comparison.
    const id = savedComparison.get();
    if (!id) return go("list");
    data.compared = [];
    data.comparing = { id, done: false, total: 0, checked: {} };
    followComparison(0);
    if (!data.list) loadList().catch(() => {});
  }
  if (data.comparing) return drawSplash(body);
  if (data.compareError) {
    if (data.compareError instanceof NeedsKey) return showKeyScreen();
    fill(body, el("div", { class: "card message" },
      el("h2", {}, "Couldn't compare prices"),
      el("p", {}, data.compareError.message),
      el("div", {}, el("button", { class: "button primary small", type: "button", onclick: startComparison }, "Try again"))));
    return;
  }
  drawComparison(body);
  // The send button needs to know which shops Thermohub is signed in to.
  loadAccounts().then(() => body.isConnected && sendAction()).catch(failed);
}

// -- The waiting screen: a magnifying glass going over both shops --

const SPLASH_LINES = [
  "Searching every size and brand",
  "Matching products to your recipes",
  "Working out how many to buy",
  "Looking for multi-buy deals",
  "Comparing like for like",
];

// Simple marks in each shop's colours, to tell them apart at a glance.
const MARKS = {
  woolworths: '<rect width="64" height="64" rx="16" style="fill: var(--woolworths)"/><text x="32" y="44" text-anchor="middle" font-size="34" font-weight="800" fill="#fff" style="font-family: var(--font)">W</text>',
  coles: '<rect width="64" height="64" rx="16" style="fill: var(--coles)"/><text x="32" y="39" text-anchor="middle" font-size="17" font-weight="800" fill="#fff" letter-spacing="-0.5" style="font-family: var(--font)">Coles</text>',
};
const MAGNIFIER = '<circle cx="34" cy="34" r="24" stroke-width="6" style="fill: var(--glass); stroke: var(--ink)"/><path d="M22 26a14 14 0 0 1 12-8" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" opacity=".8"/><path d="M52 52l20 20" stroke-width="10" stroke-linecap="round" style="stroke: var(--ink)"/>';

function svg(markup, viewBox, cls) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  node.setAttribute("viewBox", viewBox);
  node.setAttribute("class", cls);
  node.setAttribute("aria-hidden", "true");
  node.innerHTML = markup; // fixed markup only
  return node;
}

function drawSplash(body) {
  const run = data.comparing;
  const scene = el("div", { class: "scene", "aria-hidden": "true" },
    STORES.map((store) => el("div", { class: `shopfront ${store.id}`, style: `--brand: var(--${store.id})` },
      el("span", { class: "price-pop" }, "$"),
      svg(MARKS[store.id], "0 0 64 64", "mark"),
      el("div", { class: "shelf" }, el("i"), el("i"), el("i"), el("i")))),
    svg(MAGNIFIER, "0 0 80 80", "magnifier"));
  const line = el("p", { class: "splash-line" }, SPLASH_LINES[0]);
  const bars = Object.fromEntries(STORES.map((store) => {
    const fillBar = el("span", { class: "bar-fill", style: `--brand: var(--${store.id})` });
    const count = el("span", { class: "bar-count" });
    return [store.id, { row: el("div", { class: "bar-row" }, storeDot(store.id), el("span", { class: "bar-name" }, store.name), count,
      el("span", { class: "bar" }, fillBar)), fillBar, count }];
  }));
  const title = el("h1", {}, "Checking prices");
  fill(body, el("div", { class: "splash", role: "status" },
    scene,
    el("div", { class: "splash-text" }, title, line),
    el("div", { class: "bars" }, STORES.map((s) => bars[s.id].row)),
    el("p", { class: "note" }, "Long lists take a minute or two. You can leave this screen or lock your phone; Thermohub keeps going.")));
  data.splash = { body, bars, title, total: run.total };
  updateSplash();

  let n = 0;
  const turn = setInterval(() => {
    if (!line.isConnected) return clearInterval(turn);
    n = (n + 1) % SPLASH_LINES.length;
    line.classList.remove("in");
    void line.offsetWidth; // restart the fade
    line.textContent = SPLASH_LINES[n];
    line.classList.add("in");
  }, 2600);
}

function updateSplash() {
  const splash = data.splash;
  const run = data.comparing;
  if (!splash || !run || !splash.body.isConnected) return;
  const total = run.total || 0;
  splash.title.textContent = total ? `Checking ${plural(total, "item")}` : "Picking up where you left off";
  for (const store of STORES) {
    const done = Math.min((run.checked && run.checked[store.id]) || 0, total);
    const { fillBar, count } = splash.bars[store.id];
    fillBar.style.width = total ? `${Math.max(4, (done / total) * 100)}%` : "4%";
    count.textContent = total ? `${done} of ${total}` : "";
  }
}

function pick(i, storeId) {
  const match = data.comparison.items[i].matches[storeId];
  return match ? match.candidates[data.picks[i][storeId] || 0] || null : null;
}

const round2 = (n) => Math.round(n * 100) / 100;

// How many of the picked product, and what they cost.
function qty(i, storeId) {
  const p = pick(i, storeId);
  return data.qty[i][storeId] || (p ? p.packs : 1);
}

// What n of a product cost, with its multi-buy deal for each full set of them.
function costOf(p, n) {
  const deal = p.deal;
  if (deal && n >= deal.quantity) return round2(Math.floor(n / deal.quantity) * deal.price + (n % deal.quantity) * p.price);
  return round2(p.price * n);
}

function cost(i, storeId) {
  const p = pick(i, storeId);
  return p ? costOf(p, qty(i, storeId)) : null;
}

// How many of a product cover an amount (null when its size can't be read).
function coverPacks(cand, amount) {
  if (!amount) return null;
  const per = amount.kind === "pack" ? (cand.holds.mass && cand.holds.count ? cand.holds.count : 1) : cand.holds[amount.kind];
  if (!per) return null;
  return Math.min(24, Math.max(1, Math.ceil(amount.value / per - 1e-9)));
}

// How much a product delivers when bought n times, in the item's amount kind.
function delivers(cand, n, kind) {
  if (kind === "pack") return (cand.holds.mass && cand.holds.count ? cand.holds.count : 1) * n;
  return cand.holds[kind] ? cand.holds[kind] * n : null;
}

function amountKind(i, cand) {
  const amount = data.amount[i];
  if (amount) return amount.kind;
  return cand.holds.count ? "count" : cand.holds.mass ? "mass" : null;
}

// Packs of a product for the item's current amount: Thermohub's own count
// until the amount changes on this page. Results that aren't the item
// (paper towel for toilet paper) are one, at shelf price.
function packsFor(i, cand) {
  if (!cand.good_match && cand !== data.comparison.items[i].matches[cand.store].candidates[0]) return 1;
  const amount = data.amount[i];
  const original = data.comparison.items[i].amount;
  if (!amount || (original && amount.kind === original.kind && amount.value === original.value)) return cand.packs;
  return coverPacks(cand, amount) || 1;
}

// The product without its size: "Quilton Toilet Tissue 3 Ply 12 Pack" -> "quilton toilet tissue 3 ply".
const productLine = (name) => name.toLowerCase()
  .replace(/\b\d+(\.\d+)?\s*(x\s*\d+(\.\d+)?\s*)?(pack|pk|kg|g|ml|l|ea|each|ct)\b/g, " ")
  .replace(/\s+/g, " ").trim();

// The same brand and product, perhaps worded a little differently by each shop.
function sameLine(a, b) {
  const [x, y] = [productLine(a.name).split(" "), productLine(b.name).split(" ")];
  if (x[0] !== y[0]) return false;
  const [xs, ys] = [new Set(x), new Set(y)];
  const both = [...xs].filter((w) => ys.has(w)).length;
  return both / (xs.size + ys.size - both) >= 0.75;
}

// After a change at one shop, price the same amount at the other: the
// product picked there by hand in more or fewer packs; otherwise the same
// product as here if that shop has it, else its cheapest real match.
function retarget(i, from) {
  const amount = data.amount[i];
  const source = pick(i, from);
  if (!amount || !source) return;
  for (const store of STORES) {
    const current = store.id !== from && pick(i, store.id);
    if (!current) continue;
    const keep = (data.chosen[i].has(store.id) || current.tags.includes("usual")) && coverPacks(current, amount);
    if (keep) {
      data.qty[i][store.id] = keep;
      continue;
    }
    const options = data.comparison.items[i].matches[store.id].candidates
      .map((c, j) => ({ c, j, n: c.good_match && coverPacks(c, amount) }))
      .filter((o) => o.n);
    const same = options.filter((o) => sameLine(o.c, source));
    let best = null;
    (same.length ? same : options).forEach(({ c, j, n }) => {
      if (!best || costOf(c, n) < best.cost - 0.001) best = { j, n, cost: costOf(c, n) };
    });
    if (best) {
      data.picks[i][store.id] = best.j;
      data.qty[i][store.id] = best.n;
    } else {
      data.qty[i][store.id] = coverPacks(current, amount) || qty(i, store.id);
    }
  }
}

function selectProduct(i, storeId, j) {
  data.picks[i][storeId] = j;
  data.chosen[i].add(storeId);
  const cand = pick(i, storeId);
  const n = packsFor(i, cand);
  data.qty[i][storeId] = n;
  // A bigger pack than needed means more of it: price that much at the other shop too.
  const kind = amountKind(i, cand);
  const got = kind && delivers(cand, n, kind);
  if (got && (!data.amount[i] || got > data.amount[i].value + 1e-9)) data.amount[i] = { kind, value: got };
  retarget(i, storeId);
  remember(i, storeId);
}

function setQty(i, storeId, n) {
  const cand = pick(i, storeId);
  data.qty[i][storeId] = n;
  data.chosen[i].add(storeId);
  const kind = amountKind(i, cand);
  const got = kind && delivers(cand, n, kind);
  if (got) {
    data.amount[i] = { kind, value: got };
    retarget(i, storeId);
  }
  remember(i, storeId);
}

// Remember the product and how many, so Thermohub picks them first next time.
const rememberLater = {};
function remember(i, storeId) {
  const item = data.comparison.items[i];
  const p = pick(i, storeId);
  const id = `${i}:${storeId}`;
  clearTimeout(rememberLater[id]);
  rememberLater[id] = setTimeout(() => {
    const body = { store: storeId, key: item.key, product_id: p.id, name: p.name, quantity: qty(i, storeId) };
    api("choices", { method: "POST", body: JSON.stringify(body) }).catch(failed);
  }, 800);
}

// Another size of the same product that gets the same amount for at least
// 50c less: one 24 pack instead of two 12 packs.
function cheaperSameAmount(i, storeId) {
  const amount = data.amount[i];
  const current = pick(i, storeId);
  if (!amount || !current) return null;
  let best = null;
  data.comparison.items[i].matches[storeId].candidates.forEach((c, j) => {
    const n = c.good_match && sameLine(c, current) && coverPacks(c, amount);
    if (n && (!best || costOf(c, n) < best.cost - 0.001)) best = { j, n, cost: costOf(c, n), cand: c };
  });
  if (!best || best.cand.id === current.id || cost(i, storeId) - best.cost < 0.5) return null;
  return best;
}

function cheaperAt(i) {
  const [a, b] = STORES.map((s) => cost(i, s.id));
  if (a === null || b === null || Math.abs(a - b) < 0.005) return null;
  return a < b ? STORES[0].id : STORES[1].id;
}

// Where an item is bought under a plan: everything at one shop (leaving out
// what it doesn't have), or each item wherever it's cheaper.
function planStore(i, plan) {
  if (plan !== "split") return pick(i, plan) ? plan : null;
  return cheaperAt(i) || STORES.map((s) => s.id).find((s) => pick(i, s)) || null;
}

function storeFor(i) {
  const chosen = data.storeFor[i];
  return chosen && pick(i, chosen) ? chosen : planStore(i, data.plan);
}

const changedByHand = () => data.storeFor.filter((s, i) => s && s !== planStore(i, data.plan)).length;

function planTotal(plan) {
  let total = 0;
  let count = 0;
  const shops = new Set();
  const missing = [];
  data.comparison.items.forEach((item, i) => {
    const s = planStore(i, plan);
    if (!s) return missing.push(item.name);
    total += cost(i, s);
    count++;
    shops.add(s);
  });
  return { total, count, shops, missing };
}

// What will actually be bought, by shop.
function basket() {
  const orders = Object.fromEntries(STORES.map((s) => [s.id, []]));
  let total = 0;
  data.comparison.items.forEach((item, i) => {
    const s = storeFor(i);
    if (!s) return;
    orders[s].push({ item, p: pick(i, s), n: qty(i, s) });
    total += cost(i, s);
  });
  const shops = STORES.filter((s) => orders[s.id].length);
  return { orders, total, shops, count: shops.reduce((n, s) => n + orders[s.id].length, 0) };
}

// Like-for-like totals: only the items both shops have.
function summarise() {
  const totals = Object.fromEntries(STORES.map((s) => [s.id, 0]));
  const found = Object.fromEntries(STORES.map((s) => [s.id, 0]));
  let compared = 0;
  data.comparison.items.forEach((_, i) => {
    const costs = STORES.map((s) => cost(i, s.id));
    STORES.forEach((s, j) => costs[j] !== null && found[s.id]++);
    if (costs.every((c) => c !== null)) {
      compared++;
      STORES.forEach((s, j) => (totals[s.id] += costs[j]));
    }
  });
  return { totals, found, compared };
}

function drawComparison(body) {
  const c = data.comparison;
  const sum = summarise();
  const count = c.items.length;
  const [a, b] = STORES;
  const diff = Math.abs(sum.totals[a.id] - sum.totals[b.id]);
  const winner = sum.compared && diff >= 0.005 ? (sum.totals[a.id] < sum.totals[b.id] ? a : b) : null;
  const unavailable = STORES.filter((s) => c.items.some((item) => item.matches[s.id] && item.matches[s.id].error));
  const redraw = () => drawComparison(body);

  let answer;
  if (!sum.compared && unavailable.length) {
    const others = STORES.filter((s) => !unavailable.includes(s) && sum.found[s.id]);
    const names = listOf(unavailable.map((s) => s.name));
    answer = el("div", { class: "answer" },
      el("h1", {}, unavailable.length === STORES.length ? "The shops aren't responding" : `${names} isn't responding`),
      el("p", {}, others.length
        ? `Here are the ${listOf(others.map((s) => s.name))} prices. Try again in a few minutes to compare.`
        : "Try again in a few minutes."),
      el("div", {}, el("button", { class: "button secondary small", type: "button", onclick: startComparison }, "Try again")));
  } else if (!sum.compared) {
    answer = el("div", { class: "answer" }, el("h1", {}, "Nothing to compare"),
      el("p", {}, "Neither shop had a match for the same items."));
  } else if (!winner) {
    answer = el("div", { class: "answer" }, el("h1", {}, "Same price at both shops"),
      el("p", {}, `${money.format(sum.totals[a.id])} for ${sum.compared === count ? `all ${plural(count, "item")}` : `the ${plural(sum.compared, "item")} both shops have`}.`));
  } else {
    answer = el("div", { class: "answer" },
      el("h1", {}, `${winner.name} is cheaper`),
      el("span", { class: "save" }, icon("tag"), `Save ${money.format(diff)}`),
      el("p", {}, sum.compared === count ? `For all ${plural(count, "item")}.` : `For the ${plural(sum.compared, "item")} both shops have.`));
  }

  const partly = sum.compared && unavailable.length
    ? el("div", { class: "card problem-banner" },
      el("strong", {}, `Some ${listOf(unavailable.map((s) => s.name))} prices are missing`),
      el("p", {}, "That shop didn't respond for every item, so the totals only count items both shops priced."))
    : null;

  const anything = STORES.some((s) => sum.found[s.id]);
  fill(body,
    answer,
    partly,
    anything ? planPicker(redraw, winner) : null,
    el("p", { class: "section-label" }, "Your items"),
    el("ul", { class: "card rows" }, c.items.map((item, i) => itemRow(item, i, redraw))),
    el("p", { class: "note" }, "Thermohub picks products that match each ingredient: the cheapest for the amount your recipes need, or the best value per item or kilo when they don't say. Both shops are priced for the same amount, and your choices are remembered."));
  sendAction();
}

// -- Where to shop: one shop, or each item wherever it's cheaper --

function namesOf(names, most = 2) {
  const shown = names.slice(0, most).map((n) => n.toLowerCase());
  return names.length > most ? `${shown.join(", ")} and ${plural(names.length - most, "other")}` : listOf(shown);
}

function planPicker(redraw, winner) {
  const count = data.comparison.items.length;
  const single = STORES.map((s) => ({ store: s, total: planTotal(s.id) }));
  const split = planTotal("split");
  const options = single.map(({ store, total }) => ({
    id: store.id,
    label: [storeDot(store.id), store.name],
    sub: total.missing.length
      ? `${total.count} of ${count} items. No ${namesOf(total.missing)}.`
      : `All ${plural(count, "item")}`,
    total,
    badge: winner && winner.id === store.id ? "Cheaper" : null,
  }));

  // Both shops, compared fairly with the cheaper shop: on the items that shop has.
  const ref = winner || [...single].sort((x, y) => y.total.count - x.total.count || x.total.total - y.total.total)[0].store;
  const refTotal = planTotal(ref.id);
  let refSplit = 0;
  data.comparison.items.forEach((_, i) => {
    if (pick(i, ref.id)) refSplit += cost(i, planStore(i, "split"));
  });
  const saves = refTotal.total - refSplit;
  const other = STORES.find((s) => s.id !== ref.id);
  // Only worth offering when it saves at least 50c or gets items the cheaper shop doesn't have.
  if (split.shops.size > 1 && (saves >= 0.5 || split.count > refTotal.count)) {
    const parts = ["Each item where it's cheaper."];
    if (saves >= 0.005) {
      parts.push(refTotal.missing.length
        ? `${money.format(saves)} less than ${ref.name} for the items ${ref.name} has.`
        : `${money.format(saves)} less than all at ${ref.name}.`);
    }
    const gained = data.comparison.items.filter((_, i) => !pick(i, ref.id) && pick(i, other.id)).map((item) => item.name);
    if (gained.length) parts.push(`Gets ${namesOf(gained)} from ${other.name}.`);
    options.push({
      id: "split",
      label: [el("span", { class: "store-pair" }, STORES.map((s) => storeDot(s.id))), "Both shops"],
      sub: parts.join(" "),
      total: split,
      badge: null,
    });
  } else if (data.plan === "split") {
    data.plan = defaultPlan(data.comparison);
  }

  const changed = changedByHand();
  return el("fieldset", { class: "plans" },
    el("legend", { class: "section-label" }, "Where do you want to shop?"),
    el("div", { class: "card rows" }, options.map((o) => {
      const id = `plan-${o.id}`;
      const input = el("input", { type: "radio", class: "tick", name: "plan", id, value: o.id, checked: data.plan === o.id });
      // On click rather than change: picking the current option again also undoes items moved by hand.
      input.addEventListener("click", () => {
        data.plan = o.id;
        data.storeFor = data.storeFor.map(() => null);
        redraw();
      });
      return el("label", { class: `plan${data.plan === o.id ? " on" : ""}`, for: id },
        input,
        el("span", { class: "store plan-name" }, o.label),
        el("span", { class: "plan-total" }, money.format(o.total.total)),
        el("span", { class: "plan-sub" }, o.sub, o.badge ? el("span", { class: "best-label" }, icon("check"), o.badge) : null));
    })),
    changed
      ? el("p", { class: "note plan-note" }, `You've moved ${plural(changed, "item")} to the other shop. `,
        el("button", { class: "text-button", type: "button", onclick: () => {
          data.storeFor = data.storeFor.map(() => null);
          redraw();
        } }, "Undo"))
      : null);
}

// -- One item: the product it'll be, how many, and which shop --

const TAGS = {
  usual: { label: "You usually buy this", icon: "star", class: "usual" },
  best_value: { label: "Best value", icon: "tag", class: "value" },
  special: { label: "On special", icon: "tag", class: "special" },
};

function tags(p, withCheck = false) {
  const list = p.tags.map((t) => (t === "deal" && p.deal ? { label: p.deal.text, icon: "tag", class: "deal" } : TAGS[t]))
    .filter(Boolean)
    .map((t) => el("span", { class: `tag ${t.class}` }, icon(t.icon), t.label));
  if (withCheck && !p.good_match) list.push(el("span", { class: "tag check" }, icon("warn"), "Check this is right"));
  return list.length ? el("span", { class: "tags" }, list) : null;
}

function timesPrice(p, n) {
  if (p.deal && n >= p.deal.quantity) return `${n} for ${money.format(costOf(p, n))}`;
  return n > 1 ? `${n} × ${money.format(p.price)}` : money.format(p.price);
}

// Size (unless the name says it) and the price per item or kilo.
function productMeta(p) {
  let size = p.size && p.size.toLowerCase() !== "each" && !p.name.toLowerCase().includes(p.size.toLowerCase()) ? p.size : null;
  if (p.estimated && p.holds.count) size = `${size || "a pack"}, about ${p.holds.count}`;
  const value = p.value || p.unit_price;
  const valueText = value && (p.estimated && value.per === "each" ? `about ${value.text}` : value.text);
  return [size, valueText].filter(Boolean).join(" · ");
}

function stepper(i, storeId, redraw) {
  const n = qty(i, storeId);
  const change = (by) => {
    setQty(i, storeId, clamp(n + by, 1, 24));
    redraw();
  };
  return el("div", { class: "stepper", role: "group", "aria-label": "How many" },
    el("button", { type: "button", "aria-label": "One fewer", disabled: n <= 1, onclick: () => change(-1) }, icon("minus")),
    el("span", { class: "stepper-n", "aria-live": "polite" }, String(n)),
    el("button", { type: "button", "aria-label": "One more", disabled: n >= 24, onclick: () => change(1) }, icon("plus")));
}

function itemRow(item, i, redraw) {
  const s = storeFor(i);
  const p = s ? pick(i, s) : null;
  const open = data.open.has(i);
  const source = data.compared[i];
  const cheaper = cheaperAt(i);

  let product;
  let howMany = null;
  const tips = [];
  if (p) {
    const url = safeUrl(p.url);
    product = el("div", { class: "product" },
      photo(p.image),
      el("div", {},
        url ? el("a", { class: "pname", href: url, target: "_blank", rel: "noopener" }, p.name) : el("span", { class: "pname" }, p.name),
        el("p", { class: "meta" }, [timesPrice(p, qty(i, s)), productMeta(p)].filter(Boolean).join(" · ")),
        tags(p, true)));
    howMany = el("div", { class: "qty-row" }, el("span", { class: "qty-label" }, "How many"), stepper(i, s, redraw));
    const n = qty(i, s);
    if (p.deal && n < p.deal.quantity) {
      const more = p.deal.quantity - n;
      tips.push(el("div", { class: "tip-line" }, icon("tag"),
        el("span", {}, `${p.deal.text}: ${more === 1 ? "one" : more} more for ${money.format(costOf(p, p.deal.quantity) - costOf(p, n))}, saving ${money.format(p.price * p.deal.quantity - p.deal.price)}.`),
        el("button", { class: "button secondary small", type: "button", onclick: () => {
          setQty(i, s, p.deal.quantity);
          redraw();
        } }, `Make it ${p.deal.quantity}`)));
    }
    const better = cheaperSameAmount(i, s);
    if (better) {
      tips.push(el("div", { class: "tip-line" }, icon("bulb"),
        el("span", {}, `${better.n > 1 ? `${better.n} × ` : "The "}${better.cand.size || better.cand.name} gets you the same for ${money.format(cost(i, s) - better.cost)} less.`),
        el("button", { class: "button secondary small", type: "button", onclick: () => {
          data.picks[i][s] = better.j;
          data.chosen[i].add(s);
          data.qty[i][s] = better.n;
          remember(i, s);
          redraw();
        } }, "Switch")));
    }
  } else {
    const has = STORES.filter((x) => pick(i, x.id));
    product = el("div", { class: "product none" }, has.length
      ? `${storeById(data.plan).name} doesn't seem to sell this. Tap ${has[0].name} below to buy it there.`
      : item.matches[STORES[0].id].error || item.matches[STORES[1].id].error
        ? "Couldn't check the shops for this item."
        : "Neither shop seems to sell this.");
  }

  const where = el("div", { class: "switch", role: "group", "aria-label": `Where to buy ${item.name}` }, STORES.map((store) => {
    const at = cost(i, store.id);
    const on = s === store.id;
    const button = el("button", { type: "button", class: on ? "on" : "", "aria-pressed": String(on), disabled: at === null },
      storeDot(store.id),
      el("span", { class: "switch-name" }, store.name),
      el("span", { class: "switch-price" }, at === null ? "Not sold" : money.format(at),
        cheaper === store.id ? el("span", { class: "switch-cheaper" }, " Cheaper") : null));
    button.addEventListener("click", () => {
      data.storeFor[i] = store.id === planStore(i, data.plan) ? null : store.id;
      redraw();
    });
    return button;
  }));

  const candidates = s ? item.matches[s].candidates : [];
  const toggle = candidates.length > 1
    ? el("button", { class: "text-button", type: "button", "aria-expanded": String(open), onclick: () => {
      if (open) data.open.delete(i);
      else data.open.add(i);
      redraw();
    } }, open ? "Done" : `See all ${candidates.length} options`)
    : null;

  return el("li", { class: "item" },
    el("div", { class: "item-head" },
      el("span", { class: "name" }, nice(item.name)),
      el("span", { class: "cost" }, p ? money.format(cost(i, s)) : "–")),
    product,
    howMany,
    where,
    tips,
    toggle,
    open && s ? choices(item, i, s, source, redraw) : null);
}

// Every option at the shop: real matches first, best for the money first,
// each priced for the same amount; then the shop's other results.
function choices(item, i, storeId, source, redraw) {
  const store = storeById(storeId);
  const current = pick(i, storeId);
  const all = item.matches[storeId].candidates.map((cand, j) => ({ cand, j, n: packsFor(i, cand) }));
  const amount = data.amount[i];
  const byValue = (x, y) => (x.cand.value ? x.cand.value.price : Infinity) - (y.cand.value ? y.cand.value.price : Infinity) || x.cand.price - y.cand.price;
  const byCost = (x, y) => costOf(x.cand, x.n) - costOf(y.cand, y.n);
  const good = all.filter((o) => o.cand.good_match).sort(amount ? byCost : byValue);
  const others = all.filter((o) => !o.cand.good_match);
  const showAll = data.showAll.has(i);
  const shown = showAll ? [...good, ...others] : [...good, ...others].slice(0, 6);
  const hidden = good.length + others.length - shown.length;

  const option = ({ cand, j, n }) => {
    const on = cand.id === current.id;
    const button = el("button", { type: "button", class: `choice${on ? " on" : ""}`, "aria-pressed": String(on) },
      photo(cand.image),
      el("span", { class: "choice-text" },
        el("span", { class: "pname" }, cand.name),
        el("span", { class: "meta" }, productMeta(cand)),
        tags(cand)),
      el("span", { class: "choice-cost" }, money.format(costOf(cand, n)),
        n > 1 ? el("span", { class: "choice-times" }, timesPrice(cand, n)) : null));
    button.addEventListener("click", () => {
      selectProduct(i, storeId, j);
      redraw();
    });
    return button;
  };

  let note;
  if (source && source.display) note = `Your recipes need ${source.display}. Prices are for that much.`;
  else if (amount) note = "Prices are for the same amount at both shops.";
  else note = `Pick the ${store.name} product you want.`;
  const firstOther = shown.findIndex((o) => !o.cand.good_match);
  return el("div", { class: "choices" },
    el("p", { class: "note" }, note),
    shown.map((o, k) => [
      k === firstOther && good.length ? el("p", { class: "choices-label" }, "Other results") : null,
      option(o),
    ]),
    hidden > 0
      ? el("button", { class: "button secondary small", type: "button", onclick: () => {
        data.showAll.add(i);
        redraw();
      } }, `Show ${plural(hidden, "more option")}`)
      : null);
}

// -- Sending the basket to the shops' carts --

function sendAction() {
  if (screenName() !== "compare" || !data.comparison) return;
  const b = basket();
  if (!b.count) {
    showAction(el("button", { class: "button primary wide", type: "button", disabled: true }, "Nothing to send"));
    return;
  }
  const names = listOf(b.shops.map((s) => s.name));
  const total = el("span", { class: "button-total" }, money.format(b.total));
  if (!data.accounts) {
    showAction(el("button", { class: "button primary wide split", type: "button", disabled: true }, `Send to ${names}`, total));
    return;
  }
  const missing = b.shops.find((s) => !signedIn(s.id));
  if (missing) {
    showAction(el("button", { class: "button primary wide", type: "button", onclick: () => {
      data.returnTo = "compare";
      go(`signin/${missing.id}`);
    } }, icon("lock"), `Sign in to ${missing.name} to send`));
    return;
  }
  showAction(el("button", { class: "button primary wide split", type: "button", onclick: () => sendBasket(b) },
    `Send ${plural(b.count, "item")} to ${names}`, total));
}

function sendBasket(b) {
  startCart({
    orders: b.shops.map((s) => ({
      store: s.id,
      lines: b.orders[s.id].map(({ item, p, n }) => ({ product_id: p.id, name: p.name, url: p.url, quantity: n, key: item.key })),
    })),
  });
}

// -- Filling the carts --

const CART_JOB = "thermohub-cart-job";

async function startCart(request) {
  data.cartRequest = request;
  data.cartJob = null;
  data.cartError = null;
  go("cart");
  try {
    data.cartJob = await api("carts", { method: "POST", body: JSON.stringify(request) });
    try { sessionStorage.setItem(CART_JOB, data.cartJob.id); } catch { /* storage blocked */ }
  } catch (err) {
    data.cartError = err;
  }
  if (screenName() === "cart") render();
}

function showCart(root) {
  const body = el("div", { class: "stack", style: "gap: 18px" });
  root.append(header(true), body);
  if (data.cartError) {
    if (data.cartError instanceof NeedsKey) return showKeyScreen();
    fill(body, el("div", { class: "card message" },
      el("h2", {}, "Couldn't start adding to your cart"),
      el("p", {}, data.cartError.message),
      el("div", {}, el("button", { class: "button primary small", type: "button", onclick: () => startCart(data.cartRequest) }, "Try again"))));
    return;
  }
  if (data.cartJob) {
    drawCart(body);
    if (!data.cartJob.finished) followCart(body);
    return;
  }
  let saved = null;
  try { saved = sessionStorage.getItem(CART_JOB); } catch { /* storage blocked */ }
  if (!data.cartRequest && !saved) return go("list");
  fill(body, el("div", { class: "title" }, el("h1", {}, "Sending to your cart…")),
    el("div", { class: "card working" }, el("span", { class: "spinner" }), "Starting…"));
  if (!data.cartRequest && saved) {
    api(`carts/${saved}`)
      .then((job) => {
        data.cartJob = job;
        if (body.isConnected) render();
      })
      .catch((err) => {
        if (err instanceof NeedsKey) return showKeyScreen();
        try { sessionStorage.removeItem(CART_JOB); } catch { /* storage blocked */ }
        if (body.isConnected) go("list");
      });
  }
}

// Check on the cart job every couple of seconds while this screen is open.
function followCart(body) {
  setTimeout(async () => {
    if (!body.isConnected) return;
    try {
      data.cartJob = await api(`carts/${data.cartJob.id}`);
    } catch (err) {
      if (err instanceof NeedsKey) return showKeyScreen();
      if (err.status === 404) {
        data.cartJob = null;
        data.cartRequest = null;
        return body.isConnected && go("list");
      }
      return body.isConnected && followCart(body); // Thermohub blinked; keep trying
    }
    if (!body.isConnected) return;
    drawCart(body);
    if (data.cartJob.finished) data.accounts = null; // a shop may have signed out
    else followCart(body);
  }, 1500);
}

function drawCart(body) {
  const job = data.cartJob;
  const lines = job.orders.flatMap((o) => o.lines);
  const added = lines.filter((l) => l.status === "added").length;
  const carts = `${listOf(job.orders.map((o) => o.name))} cart${job.orders.length > 1 ? "s" : ""}`;
  let title;
  let sub;
  if (!job.finished) {
    title = `Adding to your ${carts}…`;
    sub = "Thermohub is adding each item on the shop's website, a few seconds each. You can leave this page; it carries on.";
  } else if (added === lines.length) {
    title = "All done!";
    sub = `${plural(added, "item")} ${added === 1 ? "is" : "are"} in your ${carts}. Check out on the shop's website or app as usual.`;
  } else if (added) {
    title = `${added} of ${plural(lines.length, "item")} added`;
    sub = "The rest need a hand. See below.";
  } else {
    title = "Nothing was added";
    sub = "See below for what went wrong.";
  }
  fill(body,
    el("div", { class: "title" }, el("h1", {}, title), el("p", {}, sub)),
    // With one shop, its cart button sits at the bottom of the screen instead.
    job.orders.map((order) => orderCard(order, job.orders.length > 1)),
    job.finished ? el("div", {}, el("a", { class: "button secondary small", href: "#list" }, "Back to shopping list")) : null);

  const done = job.orders.filter((o) => o.lines.some((l) => l.status === "added") && safeUrl(o.cart_url));
  if (job.finished && job.orders.length === 1 && done.length === 1) {
    showAction(el("a", { class: "button primary wide", href: done[0].cart_url, target: "_blank", rel: "noopener" },
      `Open my ${done[0].name} cart`, icon("open")));
  } else {
    hideAction();
  }
}

const LINE_ICON = { waiting: "circle", added: "check", failed: "warn", skipped: "minus" };
const ORDER_STATE = { waiting: "Waiting", running: "Adding…", done: "Done", needs_sign_in: "Needs you to sign in", failed: "Stopped" };

function orderCard(order, cartLink) {
  const missed = order.lines.filter((l) => l.status === "failed" || l.status === "skipped");
  const retry = missed.length
    ? el("button", { class: "button secondary small", type: "button", onclick: () => startCart({
      orders: [{
        store: order.store,
        lines: missed.map((l) => ({ product_id: l.product_id, name: l.name, url: l.url, quantity: l.quantity, key: l.key })),
      }],
    }) }, missed.length === order.lines.length ? "Try again" : `Try the ${plural(missed.length, "missed item")} again`)
    : null;

  let foot = null;
  if (order.state === "needs_sign_in") {
    foot = el("div", { class: "order-foot attention" },
      el("p", {}, `${order.name} needs you to sign in before Thermohub can add to your cart.`),
      el("div", { class: "confirm" },
        el("button", { class: "button primary small", type: "button", onclick: () => {
          data.returnTo = "cart";
          go(`signin/${order.store}`);
        } }, `Sign in to ${order.name}`),
        retry));
  } else if (order.state === "failed") {
    foot = el("div", { class: "order-foot attention" }, el("p", {}, order.message || "Something went wrong."), el("div", {}, retry));
  } else if (order.state === "done" && (missed.length || cartLink)) {
    const added = order.lines.some((l) => l.status === "added");
    foot = el("div", { class: "order-foot" }, el("div", { class: "confirm" }, retry,
      cartLink && added && safeUrl(order.cart_url) ? el("a", { class: "text-button", href: order.cart_url, target: "_blank", rel: "noopener" }, `Open my ${order.name} cart`) : null));
  }

  return el("div", { class: "card order" },
    el("div", { class: "order-head" },
      el("span", { class: "store" }, storeDot(order.store), order.name),
      el("span", { class: `order-state ${order.state}` }, ORDER_STATE[order.state] || "")),
    el("ul", { class: "rows" }, order.lines.map((line) => el("li", { class: `cart-line ${line.status}` },
      line.status === "adding" ? el("span", { class: "spinner small" }) : icon(LINE_ICON[line.status] || "circle"),
      el("span", { class: "cart-name" }, line.name),
      el("span", { class: "cart-qty" }, `× ${line.quantity}`),
      line.status === "failed" ? el("div", { class: "cart-problem" },
        el("p", {}, line.message || "Couldn't add this one."),
        safeUrl(line.url) ? el("a", { href: line.url, target: "_blank", rel: "noopener" }, `Add it yourself on the ${order.name} website`) : null,
        line.screenshot && line.screenshot.startsWith("data:image/")
          ? el("details", { class: "details" }, el("summary", {}, "What Thermohub saw"), el("img", { class: "seen", src: line.screenshot, alt: `The ${order.name} page for ${line.name}` }))
          : null) : null))),
    foot);
}

// -- Signing in to a shop, on its own website, inside Thermohub --

const SIGN_IN_FIELD = {
  password: { type: "password", placeholder: "Type your password here", autocomplete: "current-password" },
  code: { type: "text", placeholder: "Type the code here", autocomplete: "one-time-code", inputMode: "numeric" },
  number: { type: "text", placeholder: "Type the number here", autocomplete: "off", inputMode: "numeric" },
  email: { type: "email", placeholder: "Type your email here", autocomplete: "username" },
  tel: { type: "tel", placeholder: "Type your phone number here", autocomplete: "tel" },
  text: { type: "text", placeholder: "Type here", autocomplete: "off" },
};
const clamp = (n, low, high) => Math.max(low, Math.min(high, n));

function showSignIn(root, storeId) {
  const store = storeById(storeId);
  if (!store) return go("accounts");
  const back = data.returnTo || "accounts";
  const address = el("span", { class: "live-host" }, "Opening…");
  const image = el("img", { alt: `The ${store.name} website` });
  image.draggable = false;
  const cover = el("div", { class: "live-cover" }, el("span", { class: "spinner" }), `Opening the ${store.name} website…`);
  const screen = el("div", { class: "live-screen", role: "application", "aria-label": `${store.name} website. Tap to use it.` }, image, cover);
  const typing = el("input", {
    class: "typing-input", type: "text", autocapitalize: "off", autocomplete: "off",
    enterKeyHint: "go", placeholder: "Tap a box on the page first", "aria-label": `Type into the ${store.name} page`,
  });
  typing.setAttribute("autocorrect", "off");
  typing.spellcheck = false;
  const tools = el("div", { class: "live-tools" },
    el("button", { class: "text-button", type: "button", onclick: () => send({ type: "back" }) }, "Go back a page"),
    el("button", { class: "text-button", type: "button", onclick: () => send({ type: "restart" }) }, "Start again"));
  root.append(
    header({ href: `#${back}`, label: "Back" }),
    el("div", { class: "title" }, el("h1", {}, `Sign in to ${store.name}`),
      el("p", {}, `This is the ${store.name} website, running on your Thermohub. Sign in as usual. If ${store.name} sends you a code, type that in too.`)),
    el("div", { class: "live" },
      el("div", { class: "live-address" }, icon("lock"), address),
      screen,
      el("div", { class: "typing" }, typing),
      tools,
      el("p", { class: "note" }, `Thermohub stays signed in so it can fill your cart, like a browser does. You can sign out in Settings.`)));

  // The page in Thermohub's browser is the size of the box on this screen.
  const width = clamp(Math.round(screen.getBoundingClientRect().width), 320, 1280);
  const height = clamp(Math.round(window.innerHeight - 300), 480, Math.min(1400, Math.round(width * 1.9)));
  screen.style.aspectRatio = `${width} / ${height}`;

  const base = location.pathname.replace(/[^/]*$/, "");
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${base}api/stores/${store.id}/sign-in`);
  let finished = false;
  let frameUrl = null;
  const send = (message) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify(message));
  const problem = (text) => {
    finished = true;
    fill(cover, el("p", {}, text), el("button", { class: "button secondary small", type: "button", onclick: render }, "Try again"));
    cover.hidden = false;
    doneButton.disabled = true;
  };

  ws.addEventListener("open", () => send({ type: "hello", width, height, scale: Math.min(window.devicePixelRatio || 1, 2), key: accessKey }));
  ws.addEventListener("message", (event) => {
    if (typeof event.data !== "string") {
      const next = URL.createObjectURL(event.data);
      image.addEventListener("load", () => frameUrl && URL.revokeObjectURL(frameUrl), { once: true });
      const previous = frameUrl;
      frameUrl = next;
      image.src = next;
      if (previous) setTimeout(() => URL.revokeObjectURL(previous), 1000);
      if (!finished) cover.hidden = true;
      return;
    }
    const message = JSON.parse(event.data);
    if (message.type === "address") {
      try {
        const url = new URL(message.url);
        address.textContent = url.host.replace(/^www\./, "") + (url.pathname === "/" ? "" : url.pathname);
      } catch { /* not a web address */ }
    } else if (message.type === "editable") {
      fieldFocused(message.value ? message.field || "text" : null);
    } else if (message.type === "error") {
      problem(message.message);
    } else if (message.type === "result") {
      finished = true;
      data.accounts = null;
      data.status = null;
      if (message.signed_in) signedInNow();
      else problem(`${store.name} doesn't show you as signed in yet. Try again, and wait for your account page to appear before pressing the button.`);
    }
  });
  ws.addEventListener("close", () => !finished && problem("Lost the connection to Thermohub."));

  // Taps and swipes on the picture are passed to the page; swipes scroll it.
  let start = null;
  let lastY = 0;
  let scrolling = false;
  let pending = 0;
  let timer = null;
  const toPage = (event) => {
    const box = screen.getBoundingClientRect();
    return { x: ((event.clientX - box.left) / box.width) * width, y: ((event.clientY - box.top) / box.height) * height };
  };
  const scroll = (dy) => {
    pending += dy;
    timer = timer || setTimeout(() => {
      send({ type: "scroll", dy: Math.round(pending) });
      pending = 0;
      timer = null;
    }, 60);
  };
  screen.addEventListener("pointerdown", (event) => {
    start = { x: event.clientX, y: event.clientY };
    lastY = event.clientY;
    scrolling = false;
    screen.setPointerCapture(event.pointerId);
  });
  screen.addEventListener("pointermove", (event) => {
    if (!start) return;
    if (Math.abs(event.clientY - start.y) > 8) scrolling = true;
    if (scrolling) {
      scroll(((lastY - event.clientY) / screen.getBoundingClientRect().height) * height);
      lastY = event.clientY;
    }
  });
  screen.addEventListener("pointerup", (event) => {
    if (start && !scrolling && !finished) send({ type: "tap", ...toPage(event) });
    start = null;
  });
  screen.addEventListener("pointercancel", () => (start = null));
  screen.addEventListener("wheel", (event) => {
    event.preventDefault();
    scroll(event.deltaY);
  }, { passive: false });

  // Typing happens in the box under the picture and is copied to the page as it changes.
  let sent = "";
  const fieldFocused = (field) => {
    typing.value = "";
    sent = "";
    const kind = SIGN_IN_FIELD[field] || null;
    typing.type = kind ? kind.type : "text";
    typing.placeholder = kind ? kind.placeholder : "Tap a box on the page first";
    typing.autocomplete = kind ? kind.autocomplete : "off";
    typing.inputMode = (kind && kind.inputMode) || "";
    typing.parentElement.classList.toggle("ready", Boolean(kind));
    if (kind && matchMedia("(pointer: fine)").matches) typing.focus({ preventScroll: true });
  };
  typing.addEventListener("input", () => {
    const now = typing.value;
    let same = 0;
    while (same < sent.length && same < now.length && sent[same] === now[same]) same++;
    for (let k = Math.min(sent.length - same, 200); k > 0; k--) send({ type: "key", key: "Backspace" });
    if (now.length > same) send({ type: "text", text: now.slice(same) });
    sent = now;
  });
  typing.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      send({ type: "key", key: "Enter" });
      typing.value = "";
      sent = "";
    } else if (event.key === "Backspace" && !typing.value) {
      send({ type: "key", key: "Backspace" });
    }
  });

  const doneButton = el("button", { class: "button primary wide", type: "button" }, "I've signed in");
  doneButton.addEventListener("click", () => {
    doneButton.disabled = true;
    doneButton.textContent = "Checking…";
    fill(cover, el("span", { class: "spinner" }), `Checking with ${store.name}…`);
    cover.hidden = false;
    send({ type: "done" });
  });
  showAction(doneButton);

  const signedInNow = () => {
    leaveScreen = null;
    ws.close();
    hideAction();
    fill(app(),
      header({ href: `#${back}`, label: "Back" }),
      el("div", { class: "card message done" },
        el("span", { class: "big-check" }, icon("check")),
        el("h2", {}, `You're signed in to ${store.name}`),
        el("p", {}, `Thermohub can now add things to your ${store.name} cart.`)));
    showAction(el("button", { class: "button primary wide", type: "button", onclick: () => go(back) },
      back === "compare" ? "Back to prices" : back === "cart" ? "Back to your cart" : "Done"));
  };

  leaveScreen = () => {
    finished = true;
    send({ type: "cancel" });
    ws.close();
    if (frameUrl) URL.revokeObjectURL(frameUrl);
  };
}

// -- Checking a shop step by step --

function showCheck(root, storeId) {
  const store = storeById(storeId);
  if (!store) return go("accounts");
  const title = el("h1", {}, `Checking ${store.name}`);
  const status = el("p", {}, "This takes up to a minute.");
  const steps = el("ul", { class: "card rows checks" });
  const end = el("div", { class: "stack" });
  root.append(header({ href: "#accounts", label: "Settings" }), el("div", { class: "title" }, title, status), steps, end);

  let job = null;
  const draw = () => {
    fill(steps, job ? job.checks.map(checkRow) : null,
      job && job.done ? null : el("li", { class: "check-row" }, el("span", { class: "spinner small" }), el("span", {}, job && job.checks.length ? "Next step…" : "Starting…")));
    if (!job || !job.done) return;
    const bad = job.checks.filter((c) => !c.ok);
    title.textContent = bad.length ? `${store.name}: ${plural(bad.length, "problem")} found` : `${store.name} looks fine`;
    status.textContent = bad.length
      ? "Tap a step to see what Thermohub saw. Copy the report below to send to whoever helps you with Thermohub."
      : "Everything Thermohub tried worked.";
    fill(end, reportBox(store, job));
  };
  const follow = () => setTimeout(async () => {
    if (!steps.isConnected) return;
    try {
      job = await api(`stores/${storeId}/checks/${job.id}`);
    } catch (err) {
      if (err instanceof NeedsKey) return showKeyScreen();
      return follow(); // Thermohub blinked: keep asking
    }
    if (!steps.isConnected) return;
    draw();
    if (!job.done) follow();
  }, 1500);
  draw();
  api(`stores/${storeId}/checks`, { method: "POST" })
    .then((started) => {
      job = started;
      follow();
    })
    .catch((err) => {
      if (err instanceof NeedsKey) return showKeyScreen();
      status.textContent = err.message;
    });
}

function checkRow(check) {
  const shown = (v) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length);
  const facts = Object.entries(check.facts).filter(([, v]) => shown(v));
  const picture = check.screenshot && check.screenshot.startsWith("data:image/") ? check.screenshot : null;
  return el("li", { class: `check-row ${check.ok ? "ok" : "bad"}` },
    icon(check.ok ? "check" : "warn"),
    el("div", { class: "check-text" },
      el("strong", {}, check.name),
      el("p", {}, check.summary),
      facts.length || picture
        ? el("details", { class: "details" }, el("summary", {}, "What Thermohub saw"),
          el("dl", { class: "facts" }, facts.map(([k, v]) => [el("dt", {}, k), el("dd", {}, Array.isArray(v) ? v.join("\n") : String(v))])),
          picture ? el("img", { class: "seen", src: picture, alt: `What ${check.name.toLowerCase()} showed` }) : null)
        : null));
}

function reportBox(store, job) {
  const lines = [`Thermohub check: ${store.name}, ${new Date().toISOString()}`];
  for (const c of job.checks) {
    lines.push("", `[${c.ok ? "ok" : "FAIL"}] ${c.name}: ${c.summary}`);
    for (const [k, v] of Object.entries(c.facts)) lines.push(`  ${k}: ${Array.isArray(v) ? v.join(" | ") : v}`);
  }
  const text = lines.join("\n");
  const area = el("textarea", { class: "report", readOnly: true, rows: 8, value: text, "aria-label": "Report" });
  const copy = el("button", { class: "button primary small", type: "button" }, "Copy report");
  copy.addEventListener("click", async () => {
    let copied = false;
    try {
      await navigator.clipboard.writeText(text);
      copied = true;
    } catch { /* not available on a plain http address */ }
    if (!copied) {
      area.select();
      try { copied = document.execCommand("copy"); } catch { /* old way not allowed either */ }
    }
    copy.textContent = copied ? "Copied" : "Select the text above and copy it";
  });
  return [el("p", { class: "section-label" }, "Report"), area, el("div", {}, copy),
    el("p", { class: "note" }, "The report has the addresses and titles of the pages Thermohub saw, not the pictures.")];
}

// -- Settings --

function showSettings(root) {
  const cookidooRow = el("li", {}, accountRow(SERVICES[0], "Checking…", el("span", { class: "spinner" })));
  const storeRows = Object.fromEntries(STORES.map((s) => [s.id, el("li", {}, accountRow(SERVICES.find((x) => x.id === s.id), "Checking…", el("span", { class: "spinner" })))]));
  const recheck = el("button", { class: "button secondary small", type: "button" }, "Check again");

  const drawStores = () => {
    for (const s of STORES) drawStore(storeRows[s.id], s, drawStores);
  };
  const loadStores = (force) => {
    loadAccounts(force).then(drawStores).catch(failed);
    return loadStatus(force).then(drawStores).catch(failed);
  };
  recheck.addEventListener("click", async () => {
    recheck.disabled = true;
    recheck.textContent = "Checking…";
    data.accounts = null;
    data.status = null;
    drawStores();
    await Promise.all([loadStores(true), drawCookidoo(cookidooRow)]);
    recheck.disabled = false;
    recheck.textContent = "Check again";
  });

  root.append(
    header(true),
    el("div", { class: "title" }, el("h1", {}, "Settings")),
    el("div", { class: "stack" },
      el("p", { class: "section-label" }, "Accounts"),
      el("ul", { class: "card rows" }, cookidooRow, STORES.map((s) => storeRows[s.id])),
      el("p", { class: "note", style: "padding: 0 4px" }, "Comparing prices works without signing in to the shops. Sign in to a shop to send items to its cart.")),
    el("div", {}, recheck),
    el("div", { class: "stack" },
      el("p", { class: "section-label" }, "Something not working?"),
      el("div", { class: "card message" },
        el("p", {}, "Thermohub can check a shop step by step (searching, the sign-in page, whether it's signed in) and show what it saw."),
        el("div", { class: "confirm" }, STORES.map((s) => el("a", { class: "button secondary small", href: `#check/${s.id}` }, `Check ${s.name}`))))));
  drawStores();
  loadStores(false);
  drawCookidoo(cookidooRow);
}

function drawStore(row, store, redraw) {
  const service = SERVICES.find((x) => x.id === store.id);
  const c = data.status && data.status.connections.find((x) => x.id === store.id);
  const account = data.accounts && data.accounts.find((a) => a.id === store.id);
  if (!account) {
    fill(row, accountRow(service, "Checking…", el("span", { class: "spinner" })));
    return;
  }
  const prices = !c ? null : c.state === "connected" ? null
    : el("div", { class: "stack" }, el("p", { class: "note" }, `${c.summary} Thermohub will keep trying. You can still compare using the other shop.`), details(c.detail));
  if (!account.signed_in) {
    fill(row, accountRow(service, "Not signed in. Sign in to send items to your cart.",
      el("a", { class: "button secondary small", href: `#signin/${store.id}`, onclick: () => (data.returnTo = "accounts") }, "Sign in"),
      prices ? el("div", { class: "more" }, prices) : null));
    return;
  }
  const signOut = el("button", { class: "text-button", type: "button" }, "Sign out");
  const more = el("div", { class: "more" }, prices, el("div", {}, signOut));
  signOut.addEventListener("click", () => {
    fill(more, el("p", {}, `Sign out of ${store.name}? Thermohub won't be able to fill your ${store.name} cart until you sign in again.`),
      el("div", { class: "confirm" },
        el("button", { class: "button primary small", type: "button", onclick: async (event) => {
          event.currentTarget.disabled = true;
          try {
            await api(`stores/${store.id}/sign-out`, { method: "POST" });
            await loadAccounts(true);
          } catch (err) {
            return failed(err);
          }
          redraw();
        } }, "Sign out"),
        el("button", { class: "button secondary small", type: "button", onclick: redraw }, "Cancel")));
  });
  fill(row, accountRow(service, "Signed in. Thermohub can fill your cart.",
    el("span", { class: "side ok" }, icon("check"), "Signed in"), more));
}

function accountRow(service, state, side, more = null) {
  return el("div", { class: "account" },
    el("span", { class: "logo", style: `--brand: var(--${service.id})` }, icon(service.logo)),
    el("span", { class: "what" }, service.name),
    el("span", { class: "state" }, state),
    side ? el("span", { class: "side" }, side) : null,
    more);
}

async function drawCookidoo(row) {
  try {
    await loadAccount();
  } catch (err) {
    return failed(err);
  }
  const account = data.account;
  const service = SERVICES[0];
  if (!account.signed_in) {
    fill(row,
      accountRow(service, account.email ? "Cookidoo signed you out. Sign in again to see your list." : "Connect it to see your shopping list.",
        el("span", { class: "side off" }, "Not connected")),
      connectForm(() => {
        data.status = null;
        data.list = null;
        go("list");
      }));
    return;
  }
  const signOut = el("button", { class: "text-button", type: "button" }, "Sign out");
  const more = el("div", { class: "more" }, account.automatic ? el("p", { class: "note" }, "Connected using the details saved in Unraid.") : null, el("div", {}, signOut));
  signOut.addEventListener("click", () => {
    fill(more, el("p", {}, "Sign out of Cookidoo? Thermohub won't see your shopping list until you connect again."),
      el("div", { class: "confirm" },
        el("button", { class: "button primary small", type: "button", onclick: async (event) => {
          event.currentTarget.disabled = true;
          try {
            data.account = await api("cookidoo/sign-out", { method: "POST" });
          } catch (err) {
            return failed(err);
          }
          data.status = null;
          data.list = null;
          drawCookidoo(row);
        } }, "Sign out"),
        el("button", { class: "button secondary small", type: "button", onclick: () => drawCookidoo(row) }, "Cancel")));
  });
  fill(row, accountRow(service, account.email ? `Connected as ${account.email}` : "Connected",
    el("span", { class: "side ok" }, icon("check"), "Connected"), more));
}

// -- Access key --

function showKeyScreen() {
  hideAction();
  const input = el("input", { id: "access-key", type: "password", autocomplete: "current-password", required: true });
  const form = el("form", { class: "form" },
    el("label", { class: "field", for: "access-key" }, "Access key", input),
    el("button", { class: "button primary wide", type: "submit" }, "Continue"));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    accessKey = input.value.trim();
    try { localStorage.setItem(KEY_STORAGE, accessKey); } catch { /* storage blocked: lasts this visit */ }
    data.status = null;
    render();
  });
  fill(app(),
    header(),
    el("div", { class: "title" }, el("h1", {}, "Enter your access key"),
      el("p", {}, "This Thermohub has an access key. You only need to enter it once on this device.")),
    el("div", { class: "card" }, form));
  input.focus();
}

// -- Bottom action bar --

function showAction(button) {
  const bar = document.getElementById("action");
  fill(bar.firstElementChild, button);
  bar.hidden = false;
}

function hideAction() {
  document.getElementById("action").hidden = true;
}

window.addEventListener("hashchange", render);
render();
