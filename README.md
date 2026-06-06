# OnMenu

Django MVP for a restaurant delivery and pickup menu.

## MVP assumptions

- One active restaurant powers the public menu.
- Customers can choose delivery or pickup.
- Delivery uses the restaurant's flat delivery fee; pickup has no delivery fee.
- Payment is recorded as cash, card on delivery, or PIX. No online payment is charged yet.
- Menu item images use optional image URLs with a local placeholder fallback.
- Staff can manage data in Django admin and update order statuses in `/staff/orders/`.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py seed_menu
.\.venv\Scripts\python manage.py runserver
```

Open `http://127.0.0.1:8000/` for the customer menu.

Staff URLs:

- Django admin: `http://127.0.0.1:8000/admin/`
- Order dashboard: `http://127.0.0.1:8000/staff/orders/`

## Tests

```powershell
.\.venv\Scripts\python manage.py test
```
