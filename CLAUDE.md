# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands use the virtualenv Python at `.venv\Scripts\python`.

```powershell
# Run development server
.\.venv\Scripts\python manage.py runserver

# Run all tests
.\.venv\Scripts\python manage.py test

# Run a single app's tests
.\.venv\Scripts\python manage.py test menu
.\.venv\Scripts\python manage.py test cart
.\.venv\Scripts\python manage.py test orders

# Run a single test class or method
.\.venv\Scripts\python manage.py test menu.tests.MenuViewsTests.test_menu_shows_available_and_unavailable_items

# Apply migrations
.\.venv\Scripts\python manage.py migrate

# Seed sample data
.\.venv\Scripts\python manage.py seed_menu
```

## Architecture

**Django 5.2 project** with SQLite, structured as three apps: `menu`, `cart`, `orders`. Settings live in `config/`.

### Request flow

1. Customer browses `menu` — views use `menu.selectors.get_current_restaurant()` to load the single active `Restaurant` (lowest `id` among `is_active=True` rows).
2. Add-to-cart/update/remove hits `cart` views — the `Cart` class (`cart/cart.py`) stores state in the Django session under the key `settings.CART_SESSION_ID` (`"onmenu_cart"`). `cart` has no DB models.
3. Checkout hits `orders.views.checkout` — reads the `Cart`, validates `CheckoutForm`, creates `Order` + `OrderItem` rows in one `@transaction.atomic` call, then clears the cart.
4. Staff views (`/staff/orders/`) are protected by `@staff_member_required` and let staff update `Order.status`.

### Key design decisions

- **Single-restaurant**: `get_current_restaurant()` always returns the first active restaurant. All customer-facing queries are scoped to it.
- **Cart is session-only**: `{item_id: quantity}` dict in the session. No DB table for carts. `cart.context_processors.cart_summary` injects `cart_item_count` and `currency_symbol` into every template.
- **Snapshot pricing**: `OrderItem` stores `item_name` and `unit_price` at checkout time. `menu_item` FK is nullable (`SET_NULL`) so menu changes don't break historical orders.
- **Order totals are computed on save**: `Order.save()` always recalculates `total = subtotal + delivery_fee`. `OrderItem.save()` always recalculates `line_total = unit_price * quantity`.
- **Currency**: `CURRENCY_SYMBOL = 'R$'` in settings; rendered in templates via the context processor.

### URL namespaces

| Namespace | Prefix | Notable routes |
|-----------|--------|----------------|
| `menu` | `/` | `menu_list`, `item_detail` |
| `cart` | `/cart/` | `cart_detail`, `cart_add`, `cart_update`, `cart_remove` |
| `orders` | `/` | `checkout`, `confirmation`, `staff_order_list`, `staff_order_detail` |

Staff login/logout uses Django's built-in auth views; `LOGIN_REDIRECT_URL` goes to `orders:staff_order_list`.

### Templates and static

Templates are in `templates/` at the project root (not per-app). Static files are in `static/`. `MEDIA_ROOT` is `media/` and is served only in `DEBUG` mode.
