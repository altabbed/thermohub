# Thermohub

A small service that connects to [Cookidoo](https://cookidoo.com.au) and serves
your Thermomix shopping list as a web page and an HTTP API. It's meant to run
as a Docker container on Unraid (or any Docker host) on your home network.

**What it does today:**

- Pulls the shopping list and merges it into one list of things to buy.
  Ingredients needed by several recipes become one entry with totals
  ("3 brown onions"). Items you've ticked off in Cookidoo are left out.
- Prices the items you pick at Woolworths and Coles and tells you which is
  cheaper, and what splitting the shop between them would cost.
- Shows, in plain language, whether Cookidoo, Woolworths and Coles are
  connected, and lets you connect Cookidoo from a Settings page.

Open `http://<host>:8000/`; it works on a phone too.

**Planned:** sign in to your Woolworths/Coles accounts to fill the cart, and
learn your preferred products from order history (with reminders for things
you buy regularly). See [Roadmap](#roadmap).

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
  (tokens, your email and region), never your password.
- **PUID/PGID:** 99/100 (`nobody:users`), the Unraid convention for appdata
  ownership.

Keep it on your LAN. Don't port-forward it to the internet; it has your
Cookidoo session behind it.

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
| `THERMOHUB_DATA_DIR` | `/data` | Where the Cookidoo sign-in is kept |

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
- **Comparison:** one answer at the top ("Coles is cheaper, save $4.40"),
  each shop's total, a tip when buying from both would save more, and each
  item with its price at both shops. Tap an item to see the products and, if
  the match is wrong (loose onions instead of a 1 kg bag), choose another;
  the totals update.
- **Settings:** your Cookidoo account (connect or sign out) and whether
  Woolworths and Coles are working, with **Check again**.

Totals count one of each product and only include items both shops have;
recipe quantities aren't matched to pack sizes yet. Woolworths and Coles
don't need an account to compare prices; their accounts will come with cart
filling.

## How prices are looked up

- **Woolworths:** the JSON search API behind woolworths.com.au.
- **Coles:** the data embedded in coles.com.au search pages. Prices are for
  Coles' default store, which can differ a little from your local store.

Both sites block ordinary scripts, so requests go through
[`curl_cffi`](https://github.com/lexiforest/curl_cffi), which looks like Chrome
to the site, after first loading the home page like a browser would. Results
are cached for 30 minutes. If a store starts blocking your server anyway, its
card on the page turns red with the reason, and the comparison still shows the
other store.

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
written for the person; `detail` is the technical reason.

```json
{
  "connections": [
    { "id": "cookidoo", "name": "Cookidoo", "state": "connected",
      "summary": "Connected as you@example.com", "detail": null, "note": null },
    { "id": "coles", "name": "Coles", "state": "unavailable",
      "summary": "Coles isn't letting Thermohub check prices right now.",
      "detail": "Coles blocked the request (HTTP 403); its bot protection may be rejecting this server",
      "note": "You don't need to sign in to Coles to compare prices." }
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

Body: `{"items": ["brown onion", "garlic"]}` (1 to 60 names). Each name is
searched at every store. Quantities at the start of free-text names ("350 g
self-raising flour") are dropped from the search.

For each item, `matches.<store>.candidates` holds up to five available
products in the store's relevance order (name, size, price, was-price, unit
price per kg/L/each, image, link). The first is the pick. `cheaper` names the
store whose pick costs less. `totals`, `split_total` and `cheapest` summarise
the picks for the `compared` items that every store has. A store that
couldn't be searched sets `matches.<store>.error`.

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
| `thermohub/models.py` | Response models (also the OpenAPI schema) |
| `unraid/thermohub.xml` | Unraid container template |

`cookidoo-api` is pinned to an exact version on purpose. The library's public
getters drop the ingredient id, unit and category, so `cookidoo_client.py`
calls a few of its private methods to get the raw list. The tests in
`tests/test_cookidoo_client.py` run that path against a fake Cookidoo server;
run them after bumping the version.

## Roadmap

1. ~~Cookidoo shopping list API and page~~
2. ~~Woolworths and Coles price comparison~~
3. Remember the product you choose for each ingredient; match recipe
   quantities to pack sizes
4. Sign in to Woolworths/Coles and fill the online cart
5. Order history: preferred products, and "you usually buy this every N weeks"
   suggestions
