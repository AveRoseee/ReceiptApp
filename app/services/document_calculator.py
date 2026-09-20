from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
import sqlite3
from typing import Any, Mapping


MAX_INTEGER = 2**63 - 1


class DocumentCalculationError(ValueError):
    """Input atau Hasil perhitungan dokumen tidak Valid"""


def _validate_integer(
    value: object,
    label: str,
    minimum: int = 0,
    maximum: int = MAX_INTEGER,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise DocumentCalculationError(
            f"{label} harus berupa integer dari {minimum} sampai {maximum}."
        )

    return value


def _round_ratio(
    numerator: int,
    denominatior: int,
) -> int:
    with localcontext(Context(prec = 50, rounding = ROUND_HALF_UP)):
        amount = Decimal(numerator) / Decimal(denominatior)
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_line_total(
    quantity_milli: int,
    unit_price: int,
) -> int:
    quantity_milli = _validate_integer(
        quantity_milli, "Kuantitas", minimum=1
    )
    unit_price = _validate_integer(unit_price, "Harga Satuan")

    total = _round_ratio(quantity_milli * unit_price, 1000)
    return _validate_integer(total, "Total Item")


def calculate_document(
        items: list[Mapping[str, any]],
        *,
        discount_type: str = "AMOUNT",
        discount_value: int = 0,
        tax_rate_bps: int = 0,
) -> dict[str, Any]:
    if not isinstance(items, list):
        raise DocumentCalculationError("Daftar item harus berupa list.")

    if not isinstance(discount_type, str):
        raise DocumentCalculationError("Jenis diskon harus berupa teks.")

    discount_type = discount_type.strip().upper()

    if discount_type not in {"AMOUNT", "PERCENT"}:
        raise DocumentCalculationError(
            "Jenis Diskon harus berupa Amount atau Percent."
        )

    discount_value = _validate_integer(
        discount_value,
        "Nilai Diskon",
        maximum=10000 if discount_type == "PERCENT" else MAX_INTEGER,
    )

    tax_rate_bps = _validate_integer(
        tax_rate_bps, "Tarif Pajak", maximum=10000
    )

    line_totals = []
    subtotal = 0

    for position, item in enumerate(items, start = 1):
        if not isinstance(item, Mapping):
            raise DocumentCalculationError(
                f"Item ke-{position} harus berupa mapping/dictionary."
            )

        try:
            quantity_milli = item["quantity_milli"]
            unit_price = item["unit_price"]
        except KeyError as error:
            raise DocumentCalculationError(
                f"Item ke-{position} membutuhkan quantity-milli dan unit_price."
            ) from error

        line_total = calculate_line_total(quantity_milli, unit_price)
        line_totals.append(line_total)
        subtotal = _validate_integer(subtotal + line_total, "Subtotal")

    if discount_type == "PERCENT":
        discount_amount = _round_ratio(subtotal * discount_value, 10000)
    else:
        discount_amount = discount_value

    if discount_amount > subtotal:
        raise DocumentCalculationError(
            "Diskon nominal tidak boleh melebihi subtotal."
        )

    taxable_amount = subtotal - discount_amount
    tax_amount = _round_ratio(taxable_amount * tax_rate_bps, 10000)
    tax_amount = _validate_integer(tax_amount, "Jumlah Pajak")
    grand_total = _validate_integer(
        taxable_amount + tax_amount, "Total Akhir"
    )

    return {
        "line_totals": line_totals,
        "subtotal": subtotal,
        "discount_type": discount_type,
        "discount_value": discount_value,
        "discount_amount": discount_amount,
        "tax_rate_bps": tax_rate_bps,
        "tax_amount": tax_amount,
        "grand_total": grand_total,
    }