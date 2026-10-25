# Thermohub

A small service that connects to [Cookidoo](https://cookidoo.com.au) and serves
your Thermomix shopping list as a web page and an HTTP API. It's meant to run
as a Docker container on Unraid (or any Docker host) on your home network.

**What it does today:**

- Pulls the shopping list and merges it into one list of things to buy.
  Ingredients needed by several recipes become one entry with totals
  ("3 brown onions"). Items you've ticked off in Cookidoo are left out.
- Prices the items you pick at Woolworths and Coles, for the amount your
  recipes need (two 400 g tins for 800 g of tomatoes), and tells you which
  shop is cheaper.
- Lets you choose where to shop: everything at Woolworths, everything at
  Coles, or each item wherever it's cheaper, and move single items between
  shops.
- Shows every size and brand each shop has for an item, and picks one: the
  one you chose last time; else the cheapest real match for the amount your
  recipes need; else, for things recipes don't measure (toilet paper), the
  best value per item or per kg. Both shops are priced for the same amount,
  so 24 rolls are compared with 24 rolls. You can change the product and how
  many, and Thermohub remembers both.
- Counts things recipes count: loose fruit and veg sold one at a time
  ("Coles Bananas, approx 170g") are priced per item (3 bananas = 3 × $0.68),
  and bags are counted from that typical weight (a 1 kg bag is about 6).
- Counts multi-buy deals ("Pick any 2 for $13"): a deal you meet is in the
  price, and one you're short of is offered ("one more for $4.50, saving
  $2.00", **Make it 2**).
- Sends the items to your Woolworths and/or Coles cart. You sign in to the
  shops once, on their own websites, inside Thermohub.
- Shows, in plain language, whether Cookidoo, Woolworths and Coles are
  connected, with a Settings page for the accounts.

Open `http://<host>:8000/`; it works on a phone too.

**Planned:** learn your preferred products from order history, with reminders
for things you buy regularly. See [Roadmap](#roadmap).

> None of these services has a public API. Cookidoo goes through the unofficial
> [`cookidoo-api`](https://github.com/miaucl/cookidoo-api) library (the one
> behind Home Assistant's Cookidoo integration). Woolworths and Coles prices
> come from the same data their websites load, fetched with a browser-like
> HTTP client because both sites use bot protection. Any of them can change
> without notice and break that part until it's updated.

## Install on Unraid

The GitHub Actions workflow publishes the image to
`ghcr.io/altabbed/thermohub:latest` on every push to `main`. After the first
run, check that the package is public under the repo's **Packages** on GitHub,
or Unraid won't be able to pull it.

1. Add the template. In the Unraid terminal:

   ```sh
   wget -O /boot/config/plugins/dockerMan/templates-user/my-thermohub.xml \
     https://raw.githubusercontent.com/altabbed/thermohub/main/unraid/thermohub.xml
   ```

2. **Docker → Add Container → Template: thermohub**, then **Apply**. Nothing
   needs filling in. Optionally set an **Access key**; the page then asks for
   it once per browser.
3. Click the container icon → **WebUI** (or open `http://<unraid-ip>:8000/`).
   The Home page walks you through connecting Cookidoo: pick your country and
   enter the email and password you use for Cookidoo.

The template defaults:

- **Port:** 8000. Change the host port if something else (e.g. Portainer)
  already uses it.
- **Appdata:** `/mnt/user/appdata/thermohub`. It holds the Cookidoo sign-in
  (tokens, your email and region, never your password), the Woolworths and
  Coles browser profiles (their sign-in cookies), and the products you chose.
- **PUID/PGID:** 99/100 (`nobody:users`), the Unraid convention for appdata
  ownership.

Keep it on your LAN. Don't port-forward it to the internet: it has your
Cookidoo session and your Woolworths and Coles sign-ins behind it. Set an
**Access key** if other people use your network.

The image includes Chromium (about 500 MB) for the shop sign-in and cart
filling. It only runs while you sign in or while a cart is being filled, and
closes after five idle minutes; expect it to use 300 to 500 MB of memory
while it runs.

## Run anywhere else with Docker Compose

```sh
docker compose up -d --build
```

Then open `http://localhost:8000/` and connect Cookidoo from the page. Copy
`.env.example` to `.env` only if you want any of the optional settings below.

## Configuration

Everything a person needs is on the Settings page. These environment variables
(or `.env` with Compose) are all optional:

| Variable | Default | |
| --- | --- | --- |
| `THERMOHUB_API_KEY` | (unset) | Access key the page asks for once per browser; `/api/*` requests need it in an `X-API-Key` header |
| `COOKIDOO_EMAIL` / `COOKIDOO_PASSWORD` | (unset) | Sign in to Cookidoo automatically with these, until someone signs out on the page |
| `COOKIDOO_COUNTRY` / `COOKIDOO_LANGUAGE` | `au` / `en-AU` | Cookidoo site to use until one is picked on the page |
| `PUID` / `PGID` | `99` / `100` | User and group the app runs as and that owns `/data` |
| `THERMOHUB_DATA_DIR` | `/data` | Where the Cookidoo sign-in, shop browser profiles and chosen products are kept |
| `THERMOHUB_CHROMIUM` | `/usr/bin/chromium` in the image | Chromium to use for the shops; unset uses Playwright's own download |

Connecting Cookidoo on the Settings page uses the password once, to sign in.
Thermohub keeps the resulting tokens in `/data/cookidoo_token.json` (refreshed
automatically) and the email and region in `/data/cookidoo_account.json`; the
password itself is not stored. If Cookidoo ever ends the session, the Home
page says so and asks you to sign in again.

## Web page

Made for someone who isn't technical: one task per screen, large text and
tap targets, and no technical wording unless they open **Details**.

- **Shopping list (home):** everything on your Cookidoo list, already ticked.
  Untick what you don't need and press **Compare prices**. Three small chips
  at the top show whether Cookidoo, Woolworths and Coles are working; one
  turns amber when something needs a look, and tapping it opens Settings.
  On the first visit this screen asks you to connect Cookidoo instead.
- **Checking prices:** while the shops are searched, a magnifying glass goes
  over the two shops and a bar per shop counts the items checked. A long
  list takes a minute or two. The comparison runs on Thermohub, so leaving
  the screen, locking the phone or a reload doesn't lose it: the page picks
  it up again, and shows the last comparison if it's already done.
- **Comparison:** one answer at the top ("Coles is cheaper, save $4.40"),
  then **Where do you want to shop?**: all at Woolworths, all at Coles, or
  (when it saves at least 50c) both shops, each item wherever it's cheaper,
  each with its total. A shop missing items says which ("19 of 20 items.
  No green curry paste."), and both shops is compared with the cheaper shop
  on the items that shop has. Below, every item shows the product it will
  be, its price per item or kg, a **How many** stepper, and a
  Woolworths/Coles switch with both prices to move just that item.
  **See all options** lists every size and brand, real matches first and
  each priced for the same amount ("2 × $9.50"), then the shop's other
  results. Small labels explain the picks: *You usually buy this*, *Best
  value* (lowest price per item or kg), *On special*, and *Check this is
  right* when no product clearly matched.
- **Multi-buy deals:** products on a deal carry its label ("2 for $10").
  When you're buying fewer than the deal needs, the item says what the rest
  would cost and what it saves, with a **Make it 2** button; once you buy
  enough, the deal price is what's added up.
- **Same amount, same product:** changing the product or how many at one
  shop re-prices the other shop for the same amount, using the same brand
  when it has it (Quilton at both, not Quilton against a house brand). When
  a bigger size of the same product gets you the same for at least 50c less,
  a tip says so ("The 24 pack gets you the same for $4.00 less") with a
  **Switch** button.
- **Send to cart:** the button at the bottom sends the basket. If a shop
  isn't signed in yet, it says **Sign in to Coles to send** and takes you
  there first. A progress screen ticks items off as they go into the cart,
  then offers **Open my Coles cart**. Anything that couldn't be added gets a
  link to add it yourself and a picture of what Thermohub saw.
- **Shop sign-in:** the real Woolworths or Coles website, running in
  Thermohub's own browser and shown on your screen. Tap the boxes on the
  page and type in the box underneath (it turns into a password box for
  passwords, and offers texted codes on phones). Press **I've signed in**
  when your account page appears.
- **Settings:** your Cookidoo account (connect or sign out), and for each
  shop whether Thermohub is signed in (sign in or out) and whether prices
  are working, with **Check again**. Under **Something not working?**,
  **Check Woolworths** and **Check Coles** try each step against the real
  shop (search, search in Thermohub's browser with how long it takes, the
  sign-in page, signed in?) and show what each step saw, with screenshots
  and a report to copy and send.

The headline totals compare like for like: only items both shops have.

### Signing in to the shops and filling the cart

Woolworths and Coles have no way for other apps to sign in or add to a cart,
and both use bot protection, so Thermohub runs Chromium and does what a
person would: you sign in on the shop's own sign-in page (shown live in
Thermohub, including any texted code), and Thermohub keeps that browser
profile in `/data/browser/<shop>`, the way a browser stays signed in. Your
password goes to the shop, not to Thermohub, which doesn't store it.

Shops move their sign-in around: Woolworths' sign-in address now shows its
home page, with the form behind **My account** and **Log in**. So when no
sign-in form shows, Thermohub taps through those for you. Woolworths then
asks for your email first and your password on the next page. **I've signed
in** opens the shop's account page and looks for a sign-in form there (or
behind **My account**): none means you're signed in. If the shop shows bot
protection instead, Thermohub says it can't tell rather than guessing.

To fill the cart, Thermohub opens each product's page in that browser and
presses its **Add to cart** button, then steps the quantity up. This is the
part most likely to need adjusting when a shop changes its website, which is
why failed items come back with a screenshot. If the shop asks to sign in
again, the progress screen says so and offers to sign in and retry.
**Sign out** in Settings deletes the shop's browser profile.

The live sign-in only accepts connections from Thermohub's own page (so
another website open in your browser can't watch or drive it). Behind a
reverse proxy, pass the original `Host` header through and allow WebSocket
upgrades.

## How prices are looked up

- **Woolworths:** the JSON search API behind woolworths.com.au, a full page
  of 36 results. Its results group a product's sizes (12, 24 and 36 packs)
  into one tile; every size is kept. Multi-buy deals are read from the
  product's promotion labels ("2 for $10").
- **Coles:** the data embedded in coles.com.au search pages. Prices are for
  Coles' default store, which can differ a little from your local store.
  Sponsored products are kept (they're real products in Coles' own list,
  once each), and multi-buy deals come from Coles' `multiBuyPromotion`.

To see exactly what Thermohub reads from a shop for some words, open
`/api/stores/coles/search?q=bananas` (or `woolworths`) on your Thermohub.

Both sites block ordinary scripts, so requests go through
[`curl_cffi`](https://github.com/lexiforest/curl_cffi), which looks like Chrome
to the site, after first loading the home page like a browser would. Results
are cached for 30 minutes. If a shop blocks those requests anyway (or answers
with a page Thermohub can't read), Thermohub searches it in its own Chromium
instead, and keeps using the browser for that shop for 30 minutes. The
browser opens the shop's website once (passing its bot check) and makes every
search from that page, the way the website's own search box does, a few at a
time; loading the whole website for each search would take about ten times as
long. The page stays open for two minutes after the last search. Only if that
fails too does the shop's card turn red with the reason; the comparison still
shows the other shop.

## API

Interactive docs: `http://<host>:8000/docs`.

### `GET /api/shopping-list`

Everything on your Cookidoo shopping list, merged across recipes and sorted by
name.

| Query parameter | Default | |
| --- | --- | --- |
| `include_owned` | `false` | Also return items ticked off in Cookidoo |

```sh
curl -H "X-API-Key: $THERMOHUB_API_KEY" http://<host>:8000/api/shopping-list
```

```json
{
  "fetched_at": "2026-10-11T07:22:18Z",
  "items": [
    {
      "name": "garlic",
      "ingredient_ref": "com.vorwerk.ingredients.Ingredient-garlic",
      "category_ref": "ShoppingCategory-veg",
      "quantities": [{ "amount": 3.0, "amount_max": 4.0, "unit": "cloves" }],
      "display": "3-4 cloves",
      "owned": false,
      "optional": false,
      "sources": [
        {
          "item_id": "01JBS24P4D...",
          "type": "recipe",
          "recipe_id": "r100001",
          "recipe_name": "Spaghetti bolognese",
          "quantity": { "amount": 1.0, "amount_max": 2.0, "unit": "cloves" },
          "optional": false,
          "owned": false
        },
        {
          "item_id": "01JBS24P4E...",
          "type": "recipe",
          "recipe_id": "r100002",
          "recipe_name": "French onion soup",
          "quantity": { "amount": 2.0, "amount_max": null, "unit": "cloves" },
          "optional": false,
          "owned": false
        }
      ]
    }
  ],
  "recipes": [
    {
      "id": "r100001",
      "name": "Spaghetti bolognese",
      "url": "https://cookidoo.com.au/recipes/recipe/en-AU/r100001",
      "thumbnail": "https://assets.tmecosys.com/...",
      "custom": false
    }
  ]
}
```

How items are merged:

- **Recipe ingredients** merge on Cookidoo's ingredient id (`ingredient_ref`),
  so "brown onion" and "brown onions" from two recipes become one item.
  `ingredient_ref` is a stable key for remembering which supermarket product
  you buy for an ingredient.
- **Quantities** are totalled per unit. "20 g + 1 tbsp" of olive oil stays as
  two quantities; units are not converted.
- **Extra items** you added by hand, and ingredients of recipes you created
  yourself, are free text in Cookidoo. They have no `ingredient_ref` and merge
  only on an exact (case-insensitive) name match.
- `item_id` on each source is Cookidoo's id for that shopping-list entry. It's
  what's needed later to tick items off.

Errors from Cookidoo (bad password, Cookidoo down) return `502` with a
`detail` message.

### `GET /api/status`

Checks every connection live: Cookidoo (fetches the shopping list) and each
store (searches for "milk"). `state` is one of `connected`, `not_set_up`,
`needs_attention` (the person has to do something, such as sign in again) or
`unavailable` (not working right now, nothing for them to fix). `summary` is
written for the person; `detail` is the technical reason. For the shops,
`state` is about prices and `signed_in` about the cart.

```json
{
  "connections": [
    { "id": "cookidoo", "name": "Cookidoo", "state": "connected",
      "summary": "Connected as you@example.com", "detail": null, "note": null,
      "signed_in": null },
    { "id": "coles", "name": "Coles", "state": "unavailable",
      "summary": "Coles isn't letting Thermohub check prices right now.",
      "detail": "Coles blocked the request (HTTP 403); its bot protection may be rejecting this server",
      "note": "Sign in to send items to your Coles cart.", "signed_in": false }
  ]
}
```

### Cookidoo account

- `GET /api/cookidoo/account`: `signed_in`, `email`, `country`, `language`,
  and `automatic` (signed in with the environment variables).
- `GET /api/cookidoo/regions`: every Cookidoo site (`country`, `language`,
  `site`).
- `POST /api/cookidoo/sign-in` with `{"email", "password", "country",
  "language"}`: signs in and returns the account. A wrong email or password is
  a `400` with a plain-language `detail`.
- `POST /api/cookidoo/sign-out`: forgets the sign-in.

`GET /api/shopping-list` answers `409` while no Cookidoo account is connected.

### `POST /api/compare`

Body: `{"items": [{"name": "crushed tomatoes", "key": "ref:<ingredient_ref>",
"quantities": [{"amount": 800, "unit": "g"}]}]}` (1 to 60 items; plain names
also work). Each name is searched at every store. Quantities at the start of
free-text names ("350 g self-raising flour") are dropped from the search.
`quantities` (as in the shopping list) work out how many packs to buy, and
`key` (default: made from the name) identifies the ingredient for remembered
choices.

For each item, `matches.<store>.candidates` holds up to 24 products: the
pick first, then real matches (best for the money first), then the store's
other results. Each has the store's details (name, size, price, was-price,
unit price, image, link) plus `packs`, `cost` (price × packs), `good_match`,
`tags` (`usual`, `best_value`, `special`, `deal`), `deal` (`quantity`,
`price` for that many, `text`), `holds` (grams and/or a count, read from the
size; an "approx 170g" item counts as one), `estimated` (the count was
worked out from the weight and the typical single item) and `value` (price
per item or per kg/L, on the same basis for every candidate of the item, at
the deal price when the packs bought meet a deal). `cost` includes a deal
the packs meet. The pick is the product remembered
for that ingredient (with last time's count, for unmeasured items); else the
cheapest good match for the amount needed; else, when there's no amount, the
best-value good match; else the store's top result. `amount` is the amount
priced at every store: the recipes' need or, when they don't say, what the
usual or best-value pick holds. `cheaper` names the store whose pick costs
less.
`totals`, `split_total` and `cheapest` summarise the picks for the
`compared` items that every store has. A store that couldn't be searched sets
`matches.<store>.error`.

`POST /api/comparisons` takes the same body and runs the comparison in the
background, answering `202` with `{"id", "done", "total", "checked"}`
(`checked` counts the items searched so far at each store). `GET
/api/comparisons/{id}` reports progress, and once `done` carries the
comparison as `result` (or an `error`). The last 10 are kept until the
container restarts. The page uses this; a long list can take longer than a
phone keeps one request open.

`POST /api/choices` with `{"store", "key", "product_id", "name", "quantity"}`
remembers a product (and how many) as the pick for that ingredient at that
store (`/data/preferences.json`). Products that make it into a cart are
remembered the same way.

### Shops and carts

- `GET /api/stores`: for each shop, `signed_in` (Thermohub's browser is
  signed in) and `cart_url`.
- `WS /api/stores/{store}/sign-in`: the live sign-in. The first message is
  `{"type": "hello", "width", "height", "scale", "key"}` (`key` is the access
  key, if one is set). The server sends the page as binary JPEG frames, plus
  `{"type": "address", "url"}`, `{"type": "editable", "value", "field"}`
  after a tap (`field` is `password`, `code`, `email`, `tel`, `number` or
  `text`), `{"type": "result", "signed_in"}` and `{"type": "error",
  "message"}`. The page sends `tap` (`x`, `y`), `text`, `key`, `scroll`
  (`dy`), `back`, `restart`, `done` or `cancel`.
- `GET /api/stores/{store}/search?q=…`: the products Thermohub reads from
  the shop's search for those words, in the shop's order.
- `POST /api/stores/{store}/checks`: starts the shop checks and answers
  `202` with `{"id", "store", "done", "checks"}`; `GET
  /api/stores/{store}/checks/{id}` has the checks so far, each `{"name",
  "ok", "summary", "facts", "screenshot"}`.
- `POST /api/stores/{store}/sign-out`: forgets the shop sign-in.
- `POST /api/carts` with `{"orders": [{"store", "lines": [{"product_id",
  "name", "url", "quantity", "key"}]}]}`: starts adding to the carts and
  answers `202` with a job. Each `url` must be a page on that shop's site.
- `GET /api/carts/{id}`: the job's progress. Each order has a `state`
  (`waiting`, `running`, `done`, `needs_sign_in`, `failed`) and each line a
  `status` (`waiting`, `adding`, `added`, `failed`, `skipped`), with a
  `screenshot` data URL for failed lines.

### `GET /health`

Returns `{"status": "ok"}` without contacting Cookidoo. The Docker health
check uses it.

## Development

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
ruff check . && ruff format --check .
COOKIDOO_EMAIL=... COOKIDOO_PASSWORD=... THERMOHUB_DATA_DIR=./data \
  uvicorn thermohub.main:app --reload
```

| Path | |
| --- | --- |
| `thermohub/main.py` | FastAPI app, routes, API-key check |
| `thermohub/static/` | The web page: `index.html`, `app.js`, `app.css` (plain HTML/JS, no build step) |
| `thermohub/cookidoo_client.py` | Cookidoo sign-in and sign-out, saved sign-in, fetching the raw list |
| `thermohub/shopping_list.py` | Merging the raw list into `ShoppingList` |
| `thermohub/stores/` | Woolworths and Coles product search |
| `thermohub/compare.py` | Pricing items at each store and totalling |
| `thermohub/smarts.py` | Pack counts, product matching, the pick, remembered choices |
| `thermohub/browser.py` | Chromium for the shop sign-in (streamed to the page), searching in the browser, and cart filling |
| `thermohub/diagnose.py` | The shop checks, also as `python -m thermohub.diagnose` |
| `thermohub/models.py` | Response models (also the OpenAPI schema) |
| `unraid/thermohub.xml` | Unraid container template |

`tests/test_browser.py` drives real Chromium against a small fake shop
website. It uses `THERMOHUB_TEST_CHROMIUM` if set, else Playwright's own
browser (`python -m playwright install chromium`). Inside the image,
`python -m thermohub.browser` checks that Chromium starts, and
`python -m thermohub.diagnose --deep --terms milk bananas` runs the shop
checks against the real shops and prints the raw data they send
(`--shots DIR` saves screenshots). The **Check the shops** workflow runs it
on GitHub's servers whenever the store or browser code changes.

`cookidoo-api` is pinned to an exact version on purpose. The library's public
getters drop the ingredient id, unit and category, so `cookidoo_client.py`
calls a few of its private methods to get the raw list. The tests in
`tests/test_cookidoo_client.py` run that path against a fake Cookidoo server;
run them after bumping the version.

## Roadmap

1. ~~Cookidoo shopping list API and page~~
2. ~~Woolworths and Coles price comparison~~
3. ~~Remember the product you choose for each ingredient; match recipe
   quantities to pack sizes~~
4. ~~Sign in to Woolworths/Coles and fill the online cart~~
5. Order history: preferred products, and "you usually buy this every N weeks"
   suggestions
