"""Synthetic order service with parsing, calculation and presentation together."""
import json


def parse_order(raw):
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ValueError("items must be a list")
    for item in value["items"]:
        if not isinstance(item.get("cents"), int) or item["cents"] < 0:
            raise ValueError("invalid price")
    return value


def order_total(order):
    return sum(item["cents"] for item in order["items"])


def receipt(raw):
    order = parse_order(raw)
    total = order_total(order)
    return {"cents": total, "message": f"Total: {total // 100}.{total % 100:02d}"}


def currency_code():
    return "EUR"
