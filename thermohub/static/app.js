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
  if (!safeUrl(url)) return blank();
  const img = el("img", { src: url, alt: "", loading: "lazy" });
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
  comparing: null,
  compareError: null,
  picks: [],
  open: new Set(),
  account: null,
  regions: null,
};

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

function failed(err) {
  if (err instanceof NeedsKey) showKeyScreen();
  else console.error(err);
}

// ---- Screens ------------------------------------------------------------------

const SCREENS = { list: showList, compare: showCompare, accounts: showSettings };

function screenName() {
  const name = location.hash.slice(1);
  return SCREENS[name] ? name : "list";
}

function go(name) {
  if (screenName() === name) render();
  else location.hash = name;
}

function render() {
  hideAction();
  fill(app());
  window.scrollTo(0, 0);
  SCREENS[screenName()](app());
}

const app = () => document.getElementById("app");

function header(back = false) {
  if (back) {
    return el("header", { class: "header" },
      el("a", { class: "icon-button back", href: "#list" }, icon("back"), "Shopping list"));
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
  loadList().then(() => body.isConnected && drawList(body)).catch((err) => body.isConnected && listProblem(body, err));
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
  const button = el("button", { class: "button primary wide", type: "button", disabled: n === 0, onclick: startComparison },
    n ? `Compare prices for ${plural(n, "item")}` : "Tick items to compare");
  showAction(button);
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

function startComparison() {
  const names = data.list.items.map((i) => i.name).filter((n) => data.selected.has(n));
  data.comparison = null;
  data.compareError = null;
  data.open = new Set();
  data.comparing = api("compare", { method: "POST", body: JSON.stringify({ items: names }) })
    .then((comparison) => {
      data.comparison = comparison;
      data.picks = comparison.items.map(() => ({}));
    })
    .catch((err) => (data.compareError = err))
    .finally(() => {
      data.comparing = null;
      if (screenName() === "compare") render();
    });
  data.comparingCount = names.length;
  go("compare");
}

function showCompare(root) {
  const body = el("div", { class: "stack", style: "gap: 18px" });
  root.append(header(true), body);
  if (data.comparing) {
    fill(body,
      el("div", { class: "title" }, el("h1", {}, "Checking prices…"),
        el("p", {}, `Looking up ${plural(data.comparingCount, "item")} at Woolworths and Coles. This takes a few seconds.`)),
      el("div", { class: "card skeleton" }, el("div"), el("div"), el("div")));
    return;
  }
  if (data.compareError) {
    if (data.compareError instanceof NeedsKey) return showKeyScreen();
    fill(body, el("div", { class: "card message" },
      el("h2", {}, "Couldn't compare prices"),
      el("p", {}, data.compareError.message),
      el("div", {}, el("button", { class: "button primary small", type: "button", onclick: startComparison }, "Try again"))));
    return;
  }
  if (!data.comparison) return go("list");
  drawComparison(body);
}

function pick(i, storeId) {
  const match = data.comparison.items[i].matches[storeId];
  return match ? match.candidates[data.picks[i][storeId] || 0] || null : null;
}

function cheaperAt(i) {
  const [a, b] = STORES.map((s) => pick(i, s.id));
  if (!a || !b || Math.abs(a.price - b.price) < 0.005) return null;
  return a.price < b.price ? STORES[0].id : STORES[1].id;
}

function summarise() {
  const totals = Object.fromEntries(STORES.map((s) => [s.id, 0]));
  const found = Object.fromEntries(STORES.map((s) => [s.id, 0]));
  let split = 0;
  let compared = 0;
  data.comparison.items.forEach((_, i) => {
    const picks = STORES.map((s) => pick(i, s.id));
    STORES.forEach((s, j) => picks[j] && found[s.id]++);
    if (picks.every(Boolean)) {
      compared++;
      split += Math.min(...picks.map((p) => p.price));
      STORES.forEach((s, j) => (totals[s.id] += picks[j].price));
    }
  });
  return { totals, found, split, compared };
}

function drawComparison(body) {
  const c = data.comparison;
  const sum = summarise();
  const count = c.items.length;
  const [a, b] = STORES;
  const diff = Math.abs(sum.totals[a.id] - sum.totals[b.id]);
  const winner = sum.compared && diff >= 0.005 ? (sum.totals[a.id] < sum.totals[b.id] ? a : b) : null;
  const unavailable = STORES.filter((s) => c.items.some((item) => item.matches[s.id] && item.matches[s.id].error));

  let answer;
  if (!sum.compared && unavailable.length) {
    const others = STORES.filter((s) => !unavailable.includes(s) && sum.found[s.id]);
    const names = listOf(unavailable.map((s) => s.name));
    answer = el("div", { class: "answer" },
      el("h1", {}, unavailable.length === STORES.length ? "The shops aren't responding" : `${names} isn't responding`),
      el("p", {}, others.length
        ? `Here are the ${listOf(others.map((s) => s.name))} prices. Try again in a few minutes to compare.`
        : "Try again in a few minutes."),
      el("div", {}, el("button", { class: "button primary small", type: "button", onclick: startComparison }, "Try again")));
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

  const totals = sum.compared ? el("div", { class: "totals" }, STORES.map((s) =>
    el("div", { class: `card total${winner && winner.id === s.id ? " best" : ""}` },
      el("span", { class: "store" }, el("span", { class: "store-dot", style: `--brand: var(--${s.id})` }), s.name),
      el("span", { class: "amount" }, money.format(sum.totals[s.id])),
      winner && winner.id === s.id
        ? el("span", { class: "best-label" }, icon("check"), "Cheaper")
        : el("span", { class: "of" }, sum.found[s.id] === count ? `All ${count} items` : `Has ${sum.found[s.id]} of ${count}`)))) : null;

  const cheapest = Math.min(sum.totals[a.id], sum.totals[b.id]);
  const tip = sum.compared && cheapest - sum.split >= 0.5
    ? el("div", { class: "card tip" }, icon("bulb"),
      el("p", {}, `Buying each item wherever it's cheapest would cost ${money.format(sum.split)}, another ${money.format(cheapest - sum.split)} less.`))
    : null;

  const partly = sum.compared && unavailable.length
    ? el("div", { class: "card problem-banner" },
      el("strong", {}, `Some ${listOf(unavailable.map((s) => s.name))} prices are missing`),
      el("p", {}, "That shop didn't respond for every item, so the totals only count items both shops priced."))
    : null;

  const rows = c.items.map((item, i) => itemRow(item, i));

  fill(body,
    answer,
    totals,
    partly,
    tip,
    el("p", { class: "section-label" }, "Item by item"),
    el("ul", { class: "card rows" }, rows),
    el("p", { class: "note" }, "Prices are for one of each product, from each shop's website. Tap an item to see the product, or choose a different one."));
}

function itemRow(item, i) {
  const li = el("li");
  const draw = () => {
    const open = data.open.has(i);
    const cheaper = cheaperAt(i);
    const button = el("button", { class: "item-row", type: "button", "aria-expanded": String(open) },
      el("span", { class: "name" }, nice(item.name)),
      el("span", { class: "chev" }, icon("chev")),
      el("span", { class: "prices" }, STORES.map((s) => priceCell(item, i, s, cheaper === s.id))));
    button.addEventListener("click", () => {
      if (data.open.has(i)) data.open.delete(i);
      else data.open.add(i);
      draw();
    });
    fill(li, button, open ? itemDetail(item, i, () => drawComparison(li.closest(".stack"))) : null);
  };
  draw();
  return li;
}

function priceCell(item, i, store, cheaper) {
  const match = item.matches[store.id];
  const p = pick(i, store.id);
  const dot = el("span", { class: "store-dot", style: `--brand: var(--${store.id})` });
  if (!p) {
    return el("span", { class: "price" }, dot, el("span", {}, store.name),
      el("span", { class: "missing" }, match && match.error ? "No price" : "Not found"), el("span", { class: "slot" }));
  }
  return el("span", { class: `price${cheaper ? " cheaper" : ""}` }, dot,
    el("span", {}, store.name),
    el("span", { class: "value" }, money.format(p.price)),
    el("span", { class: "slot", "aria-label": cheaper ? "cheaper" : null }, cheaper ? icon("check") : null));
}

function itemDetail(item, i, redraw) {
  return el("div", { class: "item-detail" }, STORES.map((store) => {
    const match = item.matches[store.id];
    if (!match || !match.candidates.length) {
      return el("div", { class: "product none" },
        match && match.error ? `Couldn't check ${store.name} for this item.` : `${store.name} doesn't seem to sell this.`);
    }
    const p = pick(i, store.id);
    const url = safeUrl(p.url);
    const meta = [store.name, p.size, p.unit_price && p.unit_price.text].filter(Boolean).join(" · ");
    let chooser = null;
    if (match.candidates.length > 1) {
      const current = data.picks[i][store.id] || 0;
      chooser = el("select", { "aria-label": `Choose a different ${store.name} product` },
        el("option", { value: "", selected: true, disabled: true }, "Choose a different product"),
        match.candidates.map((cand, j) => el("option", { value: String(j) },
          `${j === current ? "✓ " : ""}${cand.name}${cand.size && !cand.name.includes(cand.size) ? ` ${cand.size}` : ""} — ${money.format(cand.price)}`)));
      chooser.addEventListener("change", () => {
        data.picks[i][store.id] = Number(chooser.value);
        redraw();
      });
    }
    return el("div", { class: "product" },
      photo(p.image),
      el("div", {},
        url ? el("a", { class: "pname", href: url, target: "_blank", rel: "noopener" }, p.name) : el("span", { class: "pname" }, p.name),
        el("p", { class: "meta" }, meta),
        el("p", { class: "meta" }, el("strong", {}, money.format(p.price)), p.was_price ? ` · on special, was ${money.format(p.was_price)}` : null),
        chooser));
  }));
}

// -- Settings --

function showSettings(root) {
  const cookidooRow = el("li", {}, accountRow(SERVICES[0], "Checking…", el("span", { class: "spinner" })));
  const storeRows = Object.fromEntries(STORES.map((s) => [s.id, el("li", {}, accountRow(SERVICES.find((x) => x.id === s.id), "Checking…", el("span", { class: "spinner" })))]));
  const recheck = el("button", { class: "button secondary small", type: "button" }, "Check again");

  const drawStores = (status) => {
    for (const s of STORES) {
      const c = status.connections.find((x) => x.id === s.id);
      if (!c) continue;
      const ok = c.state === "connected";
      fill(storeRows[s.id], accountRow(SERVICES.find((x) => x.id === s.id), ok ? "Prices are working" : c.summary,
        el("span", { class: `side ${ok ? "ok" : "problem"}` }, ok ? [icon("check"), "Working"] : "Not responding"),
        ok ? null : el("div", { class: "more" }, el("p", { class: "note" }, "Thermohub will keep trying. You can still compare using the other shop."), details(c.detail))));
    }
  };
  const loadStores = (force) => loadStatus(force).then(drawStores).catch(failed);
  recheck.addEventListener("click", async () => {
    recheck.disabled = true;
    recheck.textContent = "Checking…";
    for (const s of STORES) fill(storeRows[s.id], accountRow(SERVICES.find((x) => x.id === s.id), "Checking…", el("span", { class: "spinner" })));
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
      el("p", { class: "note", style: "padding: 0 4px" }, "You don't need a Woolworths or Coles account to compare prices.")),
    el("div", {}, recheck));
  loadStores(false);
  drawCookidoo(cookidooRow);
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
