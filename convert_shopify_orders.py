import csv
import sys
from collections import OrderedDict
from datetime import datetime

INPUT_FILE = sys.argv[1] if len(sys.argv) > 1 else "shopify_orders.csv"
OUTPUT_FILE = sys.argv[2] if len(sys.argv) > 2 else "woocommerce_webtoffee_orders.csv"

BASE_OUTPUT_HEADERS = [
    "order_id", "order_number",
    "order_date", "paid_date", "status",
    "shipping_total", "shipping_tax_total", "fee_total", "fee_tax_total",
    "tax_total", "cart_discount", "order_discount", "discount_total",
    "order_total", "order_subtotal", "order_currency",
    "payment_method", "payment_method_title", "transaction_id",
    "customer_ip_address", "customer_user_agent", "shipping_method",
    "customer_id", "customer_user", "customer_email",
    "billing_first_name", "billing_last_name", "billing_company",
    "billing_email", "billing_phone", "billing_address_1", "billing_address_2",
    "billing_postcode", "billing_city", "billing_state", "billing_country",
    "shipping_first_name", "shipping_last_name", "shipping_company",
    "shipping_address_1", "shipping_address_2", "shipping_postcode",
    "shipping_city", "shipping_state", "shipping_country",
    "customer_note", "wt_import_key",
    "shipping_items", "tax_items", "coupon_items", "order_notes",
    "meta:is_vat_exempt", "meta:_new_order_email_sent",
    "meta:_order_number", "meta:_shopify_order_number",
]


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------

def get(row, key):
    """Read a column, strip whitespace and the leading apostrophe
    Shopify adds to phone numbers / zip codes in exports."""
    value = (row.get(key) or "").strip()
    return value.lstrip("'").strip() if value.startswith("'") else value


def split_name(name):
    name = (name or "").strip()
    if not name:
        return "", ""
    parts = name.split()
    return parts[0], " ".join(parts[1:])


def clean_order_number(value):
    value = (value or "").strip()
    return value[1:] if value.startswith("#") else value


def money(value):
    value = (value or "").strip()
    if not value:
        return "0"
    return value.replace(",", "")


def is_zero(value):
    try:
        return float(value) == 0
    except (ValueError, TypeError):
        return True


def format_date(value):
    """Shopify: '2024-01-15 10:30:00 +0000' -> '2024-01-15 10:30:00'."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z").strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        parts = value.split()
        return " ".join(parts[:2]) if len(parts) >= 2 else value


def safe(text):
    """Pipe is the field separator in WebToffee item columns."""
    return (text or "").replace("|", "/").replace("\n", " ").replace("\r", " ")


def status_convert(financial_status, fulfillment_status="", cancelled_at=""):
    status = (financial_status or "").lower().strip()
    fulfilled = (fulfillment_status or "").lower().strip() == "fulfilled"

    # A cancelled Shopify order is cancelled, whatever its payment status
    if (cancelled_at or "").strip():
        return "cancelled"

    status_map = {
        "pending": "pending",
        "unpaid": "pending",
        "authorized": "on-hold",
        "refunded": "refunded",
        "voided": "cancelled",
    }
    if status in status_map:
        return status_map[status]

    # Paid / partially paid / partially refunded / unknown:
    # shipped orders are "completed", everything else "processing"
    return "completed" if fulfilled else "processing"


def create_line_item(row):
    name = safe(get(row, "Lineitem name"))
    sku = safe(get(row, "Lineitem sku"))
    quantity = get(row, "Lineitem quantity")

    # Skip rows that carry no product (e.g. blank continuation rows)
    if not name and not sku:
        return ""

    price = money(row.get("Lineitem price", ""))
    discount = money(row.get("Lineitem discount", ""))

    try:
        subtotal_num = float(quantity or 0) * float(price or 0)
        total_num = subtotal_num - float(discount or 0)
        subtotal = f"{subtotal_num:.2f}"
        total = f"{total_num:.2f}"
    except (ValueError, TypeError):
        subtotal = price
        total = price

    return (
        f"name:{name}"
        f"|product_id:"
        f"|sku:{sku}"
        f"|quantity:{quantity}"
        f"|total:{total}"
        f"|sub_total:{subtotal}"
    )


def create_shipping_item(row):
    shipping_method = safe(get(row, "Shipping Method"))
    if not shipping_method:
        return ""
    shipping_total = money(row.get("Shipping", ""))
    # NOTE: compare with a shipping_items value exported from your own
    # WooCommerce/WebToffee install and adjust the keys if they differ.
    return (
        f"items:{shipping_method}"
        f"|method_id:{shipping_method}"
        f"|total:{shipping_total}"
    )


def create_tax_item(row):
    tax_name = safe(get(row, "Tax 1 Name"))
    tax_value = money(row.get("Tax 1 Value", ""))

    # "0.00" is also zero, not just "0"
    if not tax_name and is_zero(tax_value):
        return ""

    return (
        f"rate_id:"
        f"|code:{tax_name}"
        f"|total:{tax_value}"
        f"|label:{tax_name}"
        f"|tax_rate_compound:"
        f"|shipping_tax_amount:"
    )


def create_coupon_item(row):
    coupon = safe(get(row, "Discount Code"))
    discount = money(row.get("Discount Amount", ""))
    if not coupon:
        return ""
    return f"code:{coupon}|amount:{discount}"


# ---------------------------------------------------------
# READ SHOPIFY CSV
# ---------------------------------------------------------

orders = OrderedDict()

try:
    infile = open(INPUT_FILE, "r", encoding="utf-8-sig", newline="")
except FileNotFoundError:
    sys.exit(f"Input file not found: {INPUT_FILE}")

with infile as f:
    reader = csv.DictReader(f)

    for row in reader:
        shopify_order = get(row, "Name")
        if not shopify_order:
            continue

        order_number = clean_order_number(shopify_order)

        # Order-level data lives on the first row of each order
        if order_number not in orders:
            billing_first, billing_last = split_name(row.get("Billing Name", ""))
            shipping_first, shipping_last = split_name(row.get("Shipping Name", ""))

            payment_reference = (
                get(row, "Payment ID") or get(row, "Payment Reference")
            )
            discount = money(row.get("Discount Amount", ""))
            payment_method = get(row, "Payment Method")

            orders[order_number] = {
                "order_number": order_number,
                # Used so WooCommerce keeps the Shopify number (see notes)
                "order_id": order_number,
                "order_date": format_date(row.get("Created at", "")),
                "paid_date": format_date(row.get("Paid at", "")),
                "status": status_convert(
                    row.get("Financial Status", ""),
                    row.get("Fulfillment Status", ""),
                    row.get("Cancelled at", ""),
                ),
                "shipping_total": money(row.get("Shipping", "")),
                "shipping_tax_total": "0",
                "fee_total": "0",
                "fee_tax_total": "0",
                "tax_total": money(row.get("Taxes", "")),
                # Same amount in all three can be double-counted by some
                # importers, so it is only stored in cart_discount and
                # discount_total.
                "cart_discount": discount,
                "order_discount": "0",
                "discount_total": discount,
                "order_total": money(row.get("Total", "")),
                "order_subtotal": money(row.get("Subtotal", "")),
                "order_currency": get(row, "Currency"),
                "payment_method": payment_method,
                "payment_method_title": payment_method,
                "transaction_id": payment_reference,
                "customer_ip_address": "",
                "customer_user_agent": "",
                "shipping_method": get(row, "Shipping Method"),
                "customer_id": "",
                "customer_user": "",
                "customer_email": get(row, "Email"),
                "billing_first_name": billing_first,
                "billing_last_name": billing_last,
                "billing_company": get(row, "Billing Company"),
                "billing_email": get(row, "Email"),
                "billing_phone": get(row, "Billing Phone") or get(row, "Phone"),
                "billing_address_1": get(row, "Billing Address1"),
                "billing_address_2": get(row, "Billing Address2"),
                "billing_postcode": get(row, "Billing Zip"),
                "billing_city": get(row, "Billing City"),
                "billing_state": get(row, "Billing Province"),
                "billing_country": get(row, "Billing Country"),
                "shipping_first_name": shipping_first,
                "shipping_last_name": shipping_last,
                "shipping_company": get(row, "Shipping Company"),
                "shipping_address_1": get(row, "Shipping Address1"),
                "shipping_address_2": get(row, "Shipping Address2"),
                "shipping_postcode": get(row, "Shipping Zip"),
                "shipping_city": get(row, "Shipping City"),
                "shipping_state": get(row, "Shipping Province"),
                "shipping_country": get(row, "Shipping Country"),
                "customer_note": get(row, "Notes"),
                "wt_import_key": get(row, "Id"),
                "shipping_items": "",
                "tax_items": "",
                "coupon_items": "",
                "order_notes": " | ".join(
                    part for part in (
                        f"Shopify order #{order_number}",
                        get(row, "Tags"),
                    ) if part
                ),
                "line_items": [],
                "meta:is_vat_exempt": "no",
                "meta:_new_order_email_sent": "TRUE",
                "meta:_order_number": order_number,
                "meta:_shopify_order_number": order_number,
            }

        order = orders[order_number]

        line_item = create_line_item(row)
        if line_item:
            order["line_items"].append(line_item)

        if not order["shipping_items"]:
            order["shipping_items"] = create_shipping_item(row)

        if not order["tax_items"]:
            order["tax_items"] = create_tax_item(row)

        if not order["coupon_items"]:
            order["coupon_items"] = create_coupon_item(row)


# ---------------------------------------------------------
# BUILD HEADERS
# ---------------------------------------------------------

max_line_items = max((len(o["line_items"]) for o in orders.values()), default=0)
line_item_headers = [f"line_item_{i}" for i in range(1, max_line_items + 1)]
OUTPUT_HEADERS = BASE_OUTPUT_HEADERS + line_item_headers


# ---------------------------------------------------------
# WRITE WOOCOMMERCE CSV
# ---------------------------------------------------------
# Plain UTF-8 (no BOM): a BOM can get glued onto the first header
# name ("order_number") and make the importer miss that column.

with open(OUTPUT_FILE, "w", encoding="utf-8", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=OUTPUT_HEADERS, extrasaction="ignore")
    writer.writeheader()

    for order in orders.values():
        for index, item in enumerate(order.pop("line_items"), start=1):
            order[f"line_item_{index}"] = item
        writer.writerow(order)


print("--------------------------------------")
print("Shopify → WooCommerce conversion done")
print("--------------------------------------")
print(f"Orders converted: {len(orders)}")
print(f"Maximum line items in one order: {max_line_items}")
print(f"Created line item columns: {', '.join(line_item_headers) or 'none'}")
print(f"Output file: {OUTPUT_FILE}")