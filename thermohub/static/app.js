"use strict";

const STORES = [
  { id: "woolworths", name: "Woolworths" },
  { id: "coles", name: "Coles" },
];
const KEY_STORAGE = "thermohub-api-key";
const money = new Intl.NumberFormat("en-AU", { style: "currency", currency: "AUD" });
const $ = (id) => document.getElementById(id);

// ---- Small helpers ------------------------------------------------------

const ICONS = {
  pot: '<path d="M4 10h16v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z"/><path d="M2 10h20"/><path d="M9 6.5C9 5 10.3 4 12 4s3 1 3 2.5"/>',
  cart: '<path d="M3 4h2.2l2.3 10.3a2 2 0 0 0 2 1.7h7.1a2 2 0 0 0 1.9-1.4L21 8H6.1"/><circle cx="10" cy="20" r="1.3"/><circle cx="17" cy="20" r="1.3"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  alert: '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><path d="M12 16.5h.01"/>',
  dash: '<circle cx="12" cy="12" r="9"/><path d="M8 12h8"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  x: '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
  arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  wave: '<path d="M7 11.5V6.8a1.5 1.5 0 0 1 3 0V11m0-3.2V5.5a1.5 1.5 0 0 1 3 0V11m0-4a1.5 1.5 0 0 1 3 0v6.5a6.5 6.5 0 0 1-6.5 6.5h-.3a6 6 0 0 1-4.6-2.2L4.3 15a1.6 1.6 0 0 1 2.4-2.1L7 13.2"/>',
};

function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name]; // constant markup only, never user data
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
  node.append(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
  return node;
}

// Like replaceChildren, but skips null/false children so optional parts can be inline.
function fill(node, ...children) {
  node.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
}

// A product or recipe photo that quietly becomes a blank circle if it fails to load.
function photo(url) {
  const blank = () => el("span", { class: "noimg" });
  if (!safeUrl(url)) return blank();
  const img = el("img", { src: url, alt: "", loading: "lazy" });
  img.addEventListener("error", () => img.replaceWith(blank()));
  return img;
}

function plural(n, word, many = `${word}s`) {
  return `${n} ${n === 1 ? word : many}`;
}

function safeUrl(value) {
  return typeof value === "string" && /^https?:\/\//.test(value) ? value : null;
}

function loading(text) {
  return el("div", { class: "loading" }, el("span", { class: "spinner" }), text);
}

function moreInfo(text, label = "More info") {
  return el("details", { class: "more" }, el("summary", {}, label), el("p", {}, text));
}

function greeting() {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

// ---- Talking to Thermohub -------------------------------------------------

class NeedsKey extends Error {}
class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

function readKey() {
  try { return localStorage.getItem(KEY_STORAGE) || ""; } catch { return ""; }
}

let apiKey = readKey();

function saveKey(key) {
  apiKey = key;
  try { localStorage.setItem(KEY_STORAGE, key); } catch { /* private browsing: lasts this visit */ }
}

async function api(path, options = {}) {
  const headers = options.body ? { "Content-Type": "application/json" } : {};
  if (apiKey) headers["X-API-Key"] = apiKey;
  let res;
  try {
    res = await fetch(`api/${path}`, { ...options, headers });
  } catch {
    throw new ApiError("Can't reach Thermohub. Check that the server is switched on and try again.", 0);
  }
  if (res.status === 401) throw new NeedsKey();
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = typeof body.detail === "string" ? body.detail : "Something went wrong. Please try again.";
    throw new ApiError(message, res.status);
  }
  return body;
}

// Connection checks can take a few seconds, so keep the last answer briefly.
const cache = { status: null, statusAt: 0, regions: null };

async function getStatus(force = false) {
  if (!force && cache.status && Date.now() - cache.statusAt < 60_000) return cache.status;
  cache.status = await api("status");
  cache.statusAt = Date.now();
  return cache.status;
}

function forgetStatus() {
  cache.status = null;
}

async function getRegions() {
  cache.regions = cache.regions || (await api("cookidoo/regions"));
  return cache.regions;
}

// ---- Routing --------------------------------------------------------------

const VIEWS = { home: renderHome, shopping: renderShopping, settings: renderSettings };

function currentRoute() {
  const name = location.hash.replace(/^#\/?/, "");
  return VIEWS[name] ? name : "home";
}

async function route() {
  const name = currentRoute();
  for (const link of document.querySelectorAll("[data-route]")) {
    const active = link.dataset.route === name;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  const view = $("view");
  fill(view);
  window.scrollTo(0, 0);
  try {
    await VIEWS[name](view);
  } catch (err) {
    if (err instanceof NeedsKey) renderKeyPrompt(view);
    else fill(view, el("div", { class: "card" }, el("p", { class: "notice bad" }, err.message)));
  }
}

function showKeyPrompt(err) {
  if (err instanceof NeedsKey) renderKeyPrompt($("view"));
  else console.error(err);
}

function renderKeyPrompt(view) {
  const input = el("input", { type: "password", autocomplete: "current-password", required: true, id: "key-input" });
  const form = el("form", { class: "form" },
    el("label", { class: "field", for: "key-input" }, "Access key", input),
    el("button", { class: "btn primary", type: "submit" }, "Continue"));
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    saveKey(input.value.trim());
    route();
  });
  fill(view, 
    el("section", { class: "hero" }, el("h1", {}, "This Thermohub is protected"),
      el("p", { class: "lead" }, "Enter the access key that was set up for it. This browser will remember it.")),
    el("div", { class: "card" }, form));
  input.focus();
}

// ---- Home -------------------------------------------------------------

const STATE = {
  connected: { label: "Connected", icon: "check" },
  not_set_up: { label: "Not set up yet", icon: "dash" },
  needs_attention: { label: "Needs your attention", icon: "alert" },
  unavailable: { label: "Not working right now", icon: "x" },
  checking: { label: "Checking…", icon: "clock" },
};
const SERVICES = [
  { id: "cookidoo", name: "Cookidoo", logo: "pot" },
  { id: "woolworths", name: "Woolworths", logo: "cart" },
  { id: "coles", name: "Coles", logo: "cart" },
];

async function renderHome(view) {
  const banner = el("div");
  const grid = el("div", { class: "account-grid" },
    SERVICES.map((s) => accountCard({ id: s.id, name: s.name, state: "checking", summary: "Checking the connection…" })));
  const listSlot = el("div");
  const recheck = el("button", { class: "btn quiet", type: "button", onclick: () => { forgetStatus(); route(); } }, "Check again");
  view.append(
    el("section", { class: "hero" }, el("h1", {}, greeting()),
      el("p", { class: "lead" }, "Here's how Thermohub is connected to your accounts.")),
    banner,
    el("section", { class: "section" }, el("div", { class: "section-head" }, el("h2", {}, "Your accounts"), recheck), grid),
    listSlot,
  );

  const status = await getStatus();
  fill(grid, ...status.connections.map(accountCard));
  const cookidoo = status.connections.find((c) => c.id === "cookidoo");
  if (cookidoo.state === "not_set_up") fill(banner, welcomeBanner());
  else if (cookidoo.state === "needs_attention") fill(banner, attentionBanner(cookidoo));
  if (cookidoo.state === "connected") renderListSummary(listSlot);
}

function stateFor(id, state) {
  if (state === "connected" && id !== "cookidoo") return { label: "Working", icon: "check" };
  return STATE[state] || STATE.checking;
}

function accountCard(c) {
  const service = SERVICES.find((s) => s.id === c.id) || { logo: "cart" };
  const state = stateFor(c.id, c.state);
  const actions = [];
  if (c.id === "cookidoo" && c.state === "connected") actions.push(el("a", { class: "btn secondary", href: "#/settings" }, "Manage"));
  if (c.id === "cookidoo" && c.state === "not_set_up") actions.push(el("a", { class: "btn primary", href: "#/settings" }, "Connect Cookidoo"));
  if (c.id === "cookidoo" && c.state === "needs_attention") actions.push(el("a", { class: "btn primary", href: "#/settings" }, "Sign in again"));
  if (c.state === "unavailable") actions.push(el("button", { class: "btn secondary", type: "button", onclick: () => { forgetStatus(); route(); } }, "Try again"));
  return el("article", { class: `card account-card ${c.state}` },
    el("div", { class: "account-top" }, el("span", { class: `logo ${c.id}` }, icon(service.logo)), el("h3", {}, c.name)),
    el("p", { class: "badge" }, icon(state.icon), state.label),
    el("p", { class: "summary" }, c.summary),
    c.note && c.state === "connected" ? el("p", { class: "note" }, c.note) : null,
    c.detail ? moreInfo(c.detail) : null,
    actions.length ? el("div", { class: "actions" }, actions) : null);
}

function welcomeBanner() {
  return el("section", { class: "banner welcome" },
    el("span", { class: "banner-icon" }, icon("wave")),
    el("div", {},
      el("h2", {}, "Welcome to Thermohub"),
      el("p", {}, "Thermohub reads the shopping list from your Thermomix's Cookidoo account and finds out whether Woolworths or Coles is cheaper."),
      el("ol", { class: "steps" },
        el("li", {}, "Connect your Cookidoo account (it takes a minute)."),
        el("li", {}, "Open your shopping list and tick what you need."),
        el("li", {}, "Press Compare prices.")),
      el("a", { class: "btn primary large", href: "#/settings" }, "Connect Cookidoo", icon("arrow"))));
}

function attentionBanner(cookidoo) {
  return el("section", { class: "banner attention" },
    el("span", { class: "banner-icon" }, icon("alert")),
    el("div", {},
      el("h2", {}, "Cookidoo needs you to sign in again"),
      el("p", {}, `${cookidoo.summary} Until then Thermohub can't see your shopping list.`),
      el("a", { class: "btn primary", href: "#/settings" }, "Sign in again")));
}

async function renderListSummary(slot) {
  const card = el("div", { class: "card" }, loading("Getting your shopping list…"));
  fill(slot, el("section", { class: "section" }, el("div", { class: "section-head" }, el("h2", {}, "Your shopping list")), card));
  let list;
  try {
    list = await api("shopping-list");
  } catch (err) {
    if (err instanceof NeedsKey) throw err;
    fill(card, el("p", { class: "notice bad" }, err.message));
    return;
  }
  if (!list.items.length) {
    fill(card, el("p", { class: "notice" }, "Your Cookidoo shopping list is empty. Add some recipes to it in Cookidoo and they'll show up here."));
    return;
  }
  fill(card, el("div", { class: "list-summary" },
    el("div", {},
      el("p", { class: "count" }, `${plural(list.items.length, "thing")} to buy for ${plural(list.recipes.length, "recipe")}`),
      el("div", { class: "recipe-chips" }, list.recipes.slice(0, 6).map((r) => el("span", { class: "chip" },
        photo(r.thumbnail),
        r.name)))),
    el("a", { class: "btn primary large", href: "#/shopping" }, "Compare prices", icon("arrow"))));
}

// ---- Settings -----------------------------------------------------------

async function renderSettings(view) {
  const cookidooCard = el("section", { class: "card" }, loading("Loading…"));
  const storeCards = Object.fromEntries(STORES.map((s) => [s.id, el("section", { class: "card" },
    settingHead("cart", s.id, s.name, `Thermohub looks up ${s.name} prices when you compare.`),
    el("div", { class: "status-line" }, statusBadge("checking")))]));
  const recheck = el("button", { class: "btn secondary", type: "button" }, "Check again");
  recheck.addEventListener("click", async () => {
    recheck.disabled = true;
    forgetStatus();
    await fillStoreCards(storeCards).finally(() => (recheck.disabled = false));
  });
  view.append(
    el("section", { class: "hero" }, el("h1", {}, "Settings"),
      el("p", { class: "lead" }, "Connect your accounts and check that everything is working.")),
    el("div", { class: "settings" },
      cookidooCard,
      Object.values(storeCards),
      el("section", { class: "card" },
        el("h2", {}, "Something not working?"),
        el("p", { class: "muted" }, "Thermohub checks each connection when you open it. Press Check again to try right now."),
        recheck,
        el("p", { class: "muted small" }, "For advanced users: ", el("a", { href: "docs" }, "API documentation"), "."))),
  );
  fillStoreCards(storeCards).catch(() => {});
  const [account, regions] = await Promise.all([api("cookidoo/account"), getRegions()]);
  renderCookidooSettings(cookidooCard, account, regions);
}

function settingHead(logo, id, name, text) {
  return el("div", { class: "setting-head" },
    el("span", { class: `logo ${id}` }, icon(logo)),
    el("div", {}, el("h2", {}, name), el("p", {}, text)));
}

function statusBadge(state, id) {
  const s = stateFor(id, state);
  return el("span", { class: state }, el("span", { class: "badge" }, icon(s.icon), s.label));
}

async function fillStoreCards(cards) {
  for (const s of STORES) fill(cards[s.id].lastChild, statusBadge("checking"));
  const status = await getStatus();
  for (const s of STORES) {
    const c = status.connections.find((x) => x.id === s.id);
    if (!c) continue;
    cards[s.id].lastChild.replaceWith(el("div", {},
      el("div", { class: "status-line" }, statusBadge(c.state, s.id), el("span", { class: "summary" }, c.summary)),
      c.detail ? moreInfo(c.detail) : null,
      el("p", { class: "note", style: "margin-top: 12px" },
        `You don't need a ${s.name} account to compare prices. Connecting it, so Thermohub can add things to your ${s.name} cart, is coming in a later update.`)));
  }
}

function regionLabel(region) {
  let country = region.country.toUpperCase();
  let language = region.language;
  try { country = new Intl.DisplayNames(["en"], { type: "region" }).of(country) || country; } catch { /* keep the code */ }
  try { language = new Intl.DisplayNames(["en"], { type: "language" }).of(language) || language; } catch { /* keep the code */ }
  return `${country} (${language})`;
}

function renderCookidooSettings(card, account, regions, justConnected = false) {
  const head = settingHead("pot", "cookidoo", "Cookidoo", "Thermohub reads your shopping list from your Cookidoo account.");
  if (account.signed_in) {
    const region = regions.find((r) => r.country === account.country && r.language === account.language);
    const signOut = el("button", { class: "btn secondary danger", type: "button" }, "Sign out");
    signOut.addEventListener("click", async () => {
      if (!confirm("Sign out of Cookidoo? Thermohub won't be able to see your shopping list until you sign in again.")) return;
      signOut.disabled = true;
      try {
        const updated = await api("cookidoo/sign-out", { method: "POST" });
        forgetStatus();
        renderCookidooSettings(card, updated, regions);
      } catch (err) {
        signOut.disabled = false;
        alert(err.message);
      }
    });
    fill(card, head,
      justConnected ? el("div", { class: "success" }, "You're connected! Thermohub can now see your shopping list.",
        el("a", { href: "#/shopping" }, "Open your shopping list")) : null,
      el("div", { class: "connected-row" }, icon("check"),
        el("span", {}, account.email ? ["Connected as ", el("strong", {}, account.email)] : "Connected")),
      region ? el("p", { class: "note", style: "margin: -6px 0 14px" }, `Cookidoo region: ${regionLabel(region)}`) : null,
      account.automatic ? el("p", { class: "note", style: "margin-bottom: 14px" }, "Signed in with the email and password saved in the Unraid container settings.") : null,
      signOut);
    return;
  }
  fill(card, head, signInForm(card, account, regions));
}

function signInForm(card, account, regions) {
  const options = regions
    .map((r) => ({ value: `${r.country}|${r.language}`, label: regionLabel(r) }))
    .sort((a, b) => a.label.localeCompare(b.label));
  const current = `${account.country}|${account.language}`;
  const region = el("select", { id: "cookidoo-region", required: true },
    options.map((o) => el("option", { value: o.value, selected: o.value === current }, o.label)));
  const email = el("input", { id: "cookidoo-email", type: "email", autocomplete: "username", required: true, value: account.email || "", placeholder: "you@example.com" });
  const password = el("input", { id: "cookidoo-password", type: "password", autocomplete: "current-password", required: true });
  const show = el("button", { class: "btn secondary", type: "button", "aria-label": "Show password" }, "Show");
  show.addEventListener("click", () => {
    const hidden = password.type === "password";
    password.type = hidden ? "text" : "password";
    show.textContent = hidden ? "Hide" : "Show";
    show.setAttribute("aria-label", hidden ? "Hide password" : "Show password");
  });
  const error = el("p", { class: "form-error", role: "alert", hidden: true });
  const submit = el("button", { class: "btn primary large", type: "submit" }, "Connect Cookidoo");
  const form = el("form", { class: "form" },
    el("label", { class: "field", for: "cookidoo-region" }, "Country", region),
    el("label", { class: "field", for: "cookidoo-email" }, "Email", email),
    el("label", { class: "field", for: "cookidoo-password" }, "Password", el("div", { class: "password" }, password, show)),
    el("p", { class: "hint" }, "Use the same email and password you use for Cookidoo or the Thermomix app. Thermohub only uses your password to sign in. It doesn't keep it."),
    error,
    el("div", {}, submit));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const [country, language] = region.value.split("|");
    error.hidden = true;
    submit.disabled = true;
    submit.textContent = "Connecting… this can take a few seconds";
    try {
      const updated = await api("cookidoo/sign-in", {
        method: "POST",
        body: JSON.stringify({ email: email.value.trim(), password: password.value, country, language }),
      });
      forgetStatus();
      renderCookidooSettings(card, updated, regions, true);
    } catch (err) {
      if (err instanceof NeedsKey) return renderKeyPrompt($("view"));
      error.textContent = err.message;
      error.hidden = false;
      submit.disabled = false;
      submit.textContent = "Connect Cookidoo";
    }
  });
  const needsAgain = account.email && !account.signed_in;
  return el("div", {},
    needsAgain ? el("p", { class: "note", style: "margin-bottom: 14px" }, "You've been signed out of Cookidoo. Enter your password to connect again.") : null,
    form);
}

// ---- Shopping list and price comparison ------------------------------------

const shop = {
  list: null,
  selected: new Set(),
  comparison: null,
  picks: [],
  comparing: false,
};

async function renderShopping(view) {
  const listBody = el("div", { id: "list-body" }, el("div", { class: "skeleton", style: "padding: 4px 16px 16px" }, el("div"), el("div"), el("div")));
  view.append(
    el("section", { class: "hero" }, el("h1", {}, "Shopping list"),
      el("p", { class: "lead" }, "Tick the things you need, then compare Woolworths and Coles.")),
    el("div", { class: "workspace" },
      el("section", { class: "panel", "aria-labelledby": "list-title" },
        el("div", { class: "panel-head" },
          el("div", {}, el("h2", { id: "list-title" }, "From Cookidoo"), el("p", { class: "sub", id: "list-sub" }, "Loading…")),
          el("div", { class: "tools" },
            el("button", { class: "linkbtn", id: "select-all", type: "button", onclick: () => setAll(true) }, "Select all"),
            el("button", { class: "linkbtn", id: "select-none", type: "button", onclick: () => setAll(false) }, "Clear"))),
        el("label", { class: "owned-toggle" },
          el("input", { type: "checkbox", id: "include-owned", onchange: () => loadList().catch(showKeyPrompt) }), "Show things I've already ticked off"),
        listBody,
        el("div", { class: "compare-bar" },
          el("span", { id: "selected-count" }, "Tick items to compare"),
          el("button", { id: "compare", class: "btn primary", type: "button", disabled: true, onclick: runComparison }, "Compare prices"))),
      el("section", { class: "panel compare-panel", id: "compare-panel", "aria-labelledby": "compare-title" },
        el("div", { class: "compare-scroll" },
          el("div", { class: "panel-head" }, el("h2", { id: "compare-title" }, "Which shop is cheaper?")),
          el("div", { id: "compare-body" },
            shop.comparison ? null : el("div", { class: "empty" },
              el("strong", {}, "Woolworths or Coles?"),
              "Tick items on your list, then press Compare prices."))))),
  );
  if (shop.comparison) renderComparison();
  await loadList();
}

async function loadList() {
  const includeOwned = $("include-owned") && $("include-owned").checked;
  $("list-sub").textContent = "Loading…";
  try {
    shop.list = await api(`shopping-list?include_owned=${includeOwned}`);
    const names = new Set(shop.list.items.map((i) => i.name));
    shop.selected = new Set([...shop.selected].filter((n) => names.has(n)));
    renderList();
  } catch (err) {
    if (err instanceof NeedsKey) throw err;
    shop.list = null;
    $("list-sub").textContent = "";
    fill($("list-body"), err.status === 409
      ? el("div", { class: "empty" }, el("strong", {}, "Connect Cookidoo first"), err.message,
        el("div", {}, el("a", { class: "btn primary", href: "#/settings" }, "Go to Settings")))
      : el("p", { class: "notice bad" }, err.message));
    renderSelection();
  }
}

function renderList() {
  const { items, recipes } = shop.list;
  $("list-sub").textContent = `${plural(items.length, "item")} for ${plural(recipes.length, "recipe")}`;
  fill($("list-body"), items.length
    ? el("ul", { class: "items" }, items.map(renderItem))
    : el("p", { class: "notice" }, "Your Cookidoo shopping list is empty. Add recipes to it in Cookidoo, then come back."));
  renderSelection();
}

function renderItem(item) {
  const from = [...new Set(item.sources.map((s) => s.recipe_name || "Added by you"))].join(" · ");
  const checkbox = el("input", { type: "checkbox", checked: shop.selected.has(item.name) });
  checkbox.addEventListener("change", () => {
    if (checkbox.checked) shop.selected.add(item.name);
    else shop.selected.delete(item.name);
    checkbox.closest(".item").classList.toggle("selected", checkbox.checked);
    renderSelection();
  });
  const classes = ["item", item.owned && "owned", shop.selected.has(item.name) && "selected"].filter(Boolean).join(" ");
  return el("li", { class: classes },
    el("label", {},
      checkbox,
      el("span", { class: "name" }, item.name,
        item.optional ? el("span", { class: "pill" }, "optional") : null,
        item.owned ? el("span", { class: "pill" }, "ticked off") : null),
      el("span", { class: "qty" }, item.display),
      el("span", { class: "from" }, from)));
}

function renderSelection() {
  if (!$("compare")) return;
  const n = shop.selected.size;
  const total = shop.list ? shop.list.items.length : 0;
  $("selected-count").textContent = n ? `${n} ticked` : "Tick items to compare";
  $("compare").disabled = n === 0 || shop.comparing;
  $("compare").textContent = shop.comparing ? "Comparing…" : "Compare prices";
  $("select-all").disabled = !total || n === total;
  $("select-none").disabled = n === 0;
}

function setAll(selected) {
  shop.selected = new Set(selected && shop.list ? shop.list.items.map((i) => i.name) : []);
  if (shop.list) renderList();
}

async function runComparison() {
  const names = shop.list.items.map((i) => i.name).filter((n) => shop.selected.has(n));
  shop.comparing = true;
  renderSelection();
  fill($("compare-body"), 
    el("div", { style: "padding: 0 16px" }, loading(`Checking ${plural(names.length, "item")} at Woolworths and Coles…`)),
    el("div", { class: "skeleton", style: "padding: 4px 16px 16px" }, el("div"), el("div"), el("div"), el("div")));
  if (window.matchMedia("(max-width: 900px)").matches) $("compare-panel").scrollIntoView({ behavior: "smooth" });
  try {
    shop.comparison = await api("compare", { method: "POST", body: JSON.stringify({ items: names }) });
    shop.picks = shop.comparison.items.map(() => ({}));
    if ($("compare-body")) renderComparison();
  } catch (err) {
    if (err instanceof NeedsKey) return renderKeyPrompt($("view"));
    if ($("compare-body")) fill($("compare-body"), el("p", { class: "notice bad" }, err.message));
  } finally {
    shop.comparing = false;
    renderSelection();
  }
}

function pick(i, storeId) {
  const match = shop.comparison.items[i].matches[storeId];
  return match ? match.candidates[shop.picks[i][storeId] || 0] || null : null;
}

function summarise() {
  const totals = Object.fromEntries(STORES.map((s) => [s.id, 0]));
  const found = Object.fromEntries(STORES.map((s) => [s.id, 0]));
  const missing = Object.fromEntries(STORES.map((s) => [s.id, []]));
  let split = 0;
  let compared = 0;
  shop.comparison.items.forEach((item, i) => {
    const picks = STORES.map((s) => pick(i, s.id));
    STORES.forEach((s, j) => (picks[j] ? found[s.id]++ : missing[s.id].push(item.name)));
    if (picks.every(Boolean)) {
      compared++;
      split += Math.min(...picks.map((p) => p.price));
      STORES.forEach((s, j) => (totals[s.id] += picks[j].price));
    }
  });
  return { totals, found, missing, split, compared };
}

function cheaperStore(i) {
  const picks = STORES.map((s) => pick(i, s.id));
  if (!picks.every(Boolean) || Math.abs(picks[0].price - picks[1].price) < 0.005) return null;
  return picks[0].price < picks[1].price ? STORES[0].id : STORES[1].id;
}

function renderComparison() {
  const c = shop.comparison;
  const sum = summarise();
  const [a, b] = STORES;
  const errors = Object.fromEntries(STORES.map((s) => [s.id,
    [...new Set(c.items.map((item) => item.matches[s.id] && item.matches[s.id].error).filter(Boolean))]]));
  const failed = STORES.filter((s) => errors[s.id].length);
  const diff = Math.abs(sum.totals[a.id] - sum.totals[b.id]);

  let verdict;
  if (!sum.compared && failed.length) {
    verdict = el("div", { class: "verdict" },
      el("h3", {}, `Couldn't get ${failed.map((s) => s.name).join(" or ")} prices`),
      el("p", {}, "That shop isn't answering right now. Try again in a little while. Prices from the other shop are below."));
  } else if (!sum.compared) {
    verdict = el("div", { class: "verdict" }, el("h3", {}, "Nothing to compare"),
      el("p", {}, "Neither shop had a match for the same item."));
  } else if (diff < 0.005) {
    verdict = el("div", { class: "verdict" }, el("h3", {}, "Same price at both"),
      el("p", {}, `${money.format(sum.totals[a.id])} for the ${plural(sum.compared, "item")} both shops have.`));
  } else {
    const winner = sum.totals[a.id] < sum.totals[b.id] ? a : b;
    const extra = Math.min(sum.totals[a.id], sum.totals[b.id]) - sum.split;
    verdict = el("div", { class: "verdict win" },
      el("h3", {}, `${winner.name} is ${money.format(diff)} cheaper`),
      el("p", {}, `For the ${plural(sum.compared, "item")} both shops have.` +
        (extra >= 0.01 ? ` Buying each item wherever it's cheaper would save another ${money.format(extra)}.` : "")));
  }

  const best = sum.compared && diff >= 0.005 ? (sum.totals[a.id] < sum.totals[b.id] ? a : b) : null;
  const totals = el("div", { class: "totals" },
    STORES.map((s) => el("div", { class: `total${best && best.id === s.id ? " best" : ""}` },
      el("div", { class: "k" }, el("span", { class: "store-dot", style: `--brand: var(--${s.id})` }), s.name),
      el("div", { class: "v" }, money.format(sum.totals[s.id])),
      el("div", { class: "s" }, `Has ${sum.found[s.id]} of ${c.items.length}`))),
    el("div", { class: "total split" },
      el("div", { class: "k" }, "Shop at both"),
      el("div", { class: "v" }, money.format(sum.split)),
      el("div", { class: "s" }, "Each item where it's cheaper")));

  const alerts = [];
  for (const s of STORES) {
    if (errors[s.id].length) {
      alerts.push(el("li", { class: "bad" }, `Couldn't get ${s.name} prices right now.`, moreInfo(errors[s.id][0])));
    } else if (sum.missing[s.id].length) {
      alerts.push(el("li", {}, `${s.name} didn't have: ${sum.missing[s.id].join(", ")}`));
    }
  }

  const head = el("div", { class: "cmp-head" }, el("span", {}, "Item"),
    STORES.map((s) => el("span", {}, el("span", { class: "store-dot", style: `--brand: var(--${s.id})` }), s.name)));
  const rows = c.items.map((item, i) => {
    const cheaper = cheaperStore(i);
    const term = Object.values(item.matches)[0]?.search_term;
    return el("div", { class: "cmp-row" },
      el("div", { class: "cmp-item" }, item.name,
        term && term.toLowerCase() !== item.name.toLowerCase() ? el("span", { class: "term" }, `searched for “${term}”`) : null),
      STORES.map((s) => renderCell(i, s, cheaper === s.id)));
  });

  fill($("compare-body"), ...[
    verdict,
    sum.compared ? totals : null,
    alerts.length ? el("ul", { class: "alerts" }, alerts) : null,
    el("div", { class: "cmp" }, head, rows),
    el("p", { class: "footnote" }, "Totals are for one of each product shown, at online prices. If Thermohub picked the wrong product, choose another from the list under it and the totals update."),
  ].filter(Boolean));
}

function renderCell(i, store, isCheaper) {
  const match = shop.comparison.items[i].matches[store.id];
  if (!match || !match.candidates.length) {
    return el("div", { class: "cell none" }, match && match.error ? "Couldn't check" : "Not found");
  }
  const p = pick(i, store.id);
  const url = safeUrl(p.url);
  const meta = [p.size, p.unit_price && p.unit_price.text].filter(Boolean).join(" · ");
  const name = url ? el("a", { class: "pname", href: url, target: "_blank", rel: "noopener" }, p.name) : el("span", { class: "pname" }, p.name);
  const tags = [];
  if (isCheaper) tags.push(el("span", { class: "tag good" }, "Cheaper"));
  if (p.on_special) tags.push(el("span", { class: "tag special" }, "On special"));

  let chooser = null;
  if (match.candidates.length > 1) {
    chooser = el("select", { "aria-label": `Choose a different ${store.name} product for ${shop.comparison.items[i].name}` },
      match.candidates.map((cand, j) => el("option", { value: String(j), selected: j === (shop.picks[i][store.id] || 0) },
        `${cand.name}${cand.size && !cand.name.includes(cand.size) ? ` ${cand.size}` : ""} — ${money.format(cand.price)}`)));
    chooser.addEventListener("change", () => {
      shop.picks[i][store.id] = Number(chooser.value);
      renderComparison();
    });
  }
  return el("div", { class: `cell${isCheaper ? " cheaper" : ""}` },
    el("div", { class: "product" },
      photo(p.image),
      el("div", {}, name, meta ? el("div", { class: "pmeta" }, meta) : null),
      el("div", { class: "pprice" }, money.format(p.price), p.was_price ? el("span", { class: "was" }, money.format(p.was_price)) : null)),
    tags.length ? el("div", { class: "tags" }, tags) : null,
    chooser);
}

// ---- Start ------------------------------------------------------------------

window.addEventListener("hashchange", route);
route();
