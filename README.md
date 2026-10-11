# Thermohub

A small service that connects to [Cookidoo](https://cookidoo.com.au) and serves
your Thermomix shopping list as a web page and an HTTP API. It's meant to run
as a Docker container on Unraid (or any Docker host) on your home network.

**What it does today:** it pulls the shopping list and merges it into one list
of things to buy. Ingredients needed by several recipes become one entry with
totals ("3 brown onions"). Items you've ticked off in Cookidoo are left out.
Open `http://<host>:8000/` to see the list; it works on a phone.

**Planned:** decide whether to buy each item at Woolworths or Coles, fill the
cart, and learn your preferred products from order history (with reminders for
things you buy regularly). See [Roadmap](#roadmap).

> Cookidoo has no public API. This uses the unofficial
> [`cookidoo-api`](https://github.com/miaucl/cookidoo-api) library (the one
> behind Home Assistant's Cookidoo integration), so a Cookidoo change can break
> it until the library catches up.

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

2. **Docker → Add Container → Template: thermohub**.
3. Fill in your Cookidoo email and password. Setting an **API key** is
   recommended, since anything on your network can reach the container.
4. **Apply**. Click the container icon → **WebUI** to open the shopping list
   at `http://<unraid-ip>:8000/`. If you set an API key, the page asks for it
   once and remembers it in that browser.

The template defaults:

- **Port:** 8000. Change the host port if something else (e.g. Portainer)
  already uses it.
- **Appdata:** `/mnt/user/appdata/thermohub`. It only holds the Cookidoo login
  tokens.
- **PUID/PGID:** 99/100 (`nobody:users`), the Unraid convention for appdata
  ownership.

Keep it on your LAN. Don't port-forward it to the internet; it has your
Cookidoo session behind it.

## Run anywhere else with Docker Compose

```sh
cp .env.example .env   # then fill in your Cookidoo email and password
docker compose up -d --build
curl http://localhost:8000/api/shopping-list
```

## Configuration

All configuration is environment variables (or `.env` with Compose).

| Variable | Default | |
| --- | --- | --- |
| `COOKIDOO_EMAIL` | (required) | Cookidoo login email |
| `COOKIDOO_PASSWORD` | (required) | Cookidoo password |
| `COOKIDOO_COUNTRY` | `au` | Cookidoo site country |
| `COOKIDOO_LANGUAGE` | `en-AU` | Cookidoo site language |
| `THERMOHUB_API_KEY` | (unset) | If set, `/api/*` requests need an `X-API-Key: <key>` header |
| `PUID` / `PGID` | `99` / `100` | User and group the app runs as and that owns `/data` |
| `THERMOHUB_DATA_DIR` | `/data` | Where the Cookidoo login tokens are stored |

On first use the service logs in with your email and password. It saves the
resulting tokens to `/data/cookidoo_token.json` and refreshes them from then
on, so restarts don't need a fresh password login.

## Web page

`GET /` is the shopping list page: every item with its total and the recipes
that need it, the recipes on the list (linking to Cookidoo), and a toggle for
ticked-off items. It's read-only for now. It reads the API below, so it shows
the same data.

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
| `thermohub/static/index.html` | The web page (plain HTML/JS, no build step) |
| `thermohub/cookidoo_client.py` | Cookidoo login, token persistence, fetching the raw list |
| `thermohub/shopping_list.py` | Merging the raw list into `ShoppingList` |
| `thermohub/models.py` | Response models (also the OpenAPI schema) |
| `unraid/thermohub.xml` | Unraid container template |

`cookidoo-api` is pinned to an exact version on purpose. The library's public
getters drop the ingredient id, unit and category, so `cookidoo_client.py`
calls a few of its private methods to get the raw list. The tests in
`tests/test_cookidoo_client.py` run that path against a fake Cookidoo server;
run them after bumping the version.

## Roadmap

1. ~~Cookidoo shopping list API and page~~
2. Woolworths and Coles product search; map each ingredient to products
3. Decide where to shop (price, availability, splitting the basket)
4. Fill the online cart
5. Order history: preferred products, and "you usually buy this every N weeks"
   suggestions
