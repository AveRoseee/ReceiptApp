from copy import deepcopy
from decimal import localcontext

import pytest

from app.services.document_calculator import (
    MAX_INTEGER,
    DocumentCalculationError,
    calculate_document,
    calculate_line_total,
)


def test_fractional_quantity():
    assert calculate_line_total(1500, 100000) == 150000


@pytest.mark.parametrize(
    ("quantity_milli", "unit_price", "expected"),
    [
        (499, 1, 0),
        (500, 1, 1),
        (1500, 1, 2),
        (1500, 100001, 150002),
        (1000, 0, 0),
    ],
)
def test_line_rounding(quantity_milli, unit_price, expected):
    assert calculate_line_total(quantity_milli, unit_price) == expected


def test_round_each_line_before_summing():
    result = calculate_document(
        [
            {"quantity_milli": 500, "unit_price": 1},
            {"quantity_milli": 500, "unit_price": 1},
        ]
    )

    assert result["line_totals"] == [1, 1]
    assert result["subtotal"] == 2
    assert result["grand_total"] == 2


def test_percentage_discount_and_tax():
    result = calculate_document(
        [
            {"quantity_milli": 1500, "unit_price": 100000},
            {"quantity_milli": 2000, "unit_price": 25000},
        ],
        discount_type="PERCENT",
        discount_value=1000,
        tax_rate_bps=1100,
    )

    assert result == {
        "line_totals": [150000, 50000],
        "subtotal": 200000,
        "discount_type": "PERCENT",
        "discount_value": 1000,
        "discount_amount": 20000,
        "tax_rate_bps": 1100,
        "tax_amount": 19800,
        "grand_total": 199800,
    }


def test_amount_discount_then_tax():
    result = calculate_document(
        [{"quantity_milli": 1000, "unit_price": 100000}],
        discount_type="AMOUNT",
        discount_value=25000,
        tax_rate_bps=1000,
    )

    assert result["discount_amount"] == 25000
    assert result["tax_amount"] == 7500
    assert result["grand_total"] == 82500


def test_discount_and_tax_rounding():
    result = calculate_document(
        [{"quantity_milli": 1000, "unit_price": 101}],
        discount_type="PERCENT",
        discount_value=5000,
        tax_rate_bps=100,
    )

    # Diskon 50,5 dibulatkan menjadi 51.
    # Dasar pajak = 101 - 51 = 50.
    # Pajak 0,5 dibulatkan menjadi 1.
    assert result["discount_amount"] == 51
    assert result["tax_amount"] == 1
    assert result["grand_total"] == 51


def test_full_discount_results_in_zero_total():
    result = calculate_document(
        [{"quantity_milli": 1000, "unit_price": 100000}],
        discount_type="PERCENT",
        discount_value=10000,
        tax_rate_bps=1100,
    )

    assert result["discount_amount"] == 100000
    assert result["tax_amount"] == 0
    assert result["grand_total"] == 0


def test_empty_draft():
    result = calculate_document([])

    assert result["line_totals"] == []
    assert result["subtotal"] == 0
    assert result["grand_total"] == 0


@pytest.mark.parametrize(
    "quantity",
    [0, -1, True, 1.5, "1500", None, MAX_INTEGER + 1],
)
def test_invalid_quantity_is_rejected(quantity):
    with pytest.raises(DocumentCalculationError):
        calculate_line_total(quantity, 100000)


@pytest.mark.parametrize(
    "price",
    [-1, True, 1.5, "100000", None, MAX_INTEGER + 1],
)
def test_invalid_price_is_rejected(price):
    with pytest.raises(DocumentCalculationError):
        calculate_line_total(1000, price)


@pytest.mark.parametrize(
    "options",
    [
        {"discount_type": "OTHER"},
        {"discount_type": None},
        {"discount_value": -1},
        {"discount_value": True},
        {"discount_value": "1000"},
        {"discount_value": 1.5},
        {"discount_value": MAX_INTEGER + 1},
        {"discount_value": 101},
        {"discount_type": "PERCENT", "discount_value": 10001},
        {"tax_rate_bps": -1},
        {"tax_rate_bps": 10001},
        {"tax_rate_bps": True},
        {"tax_rate_bps": 1.5},
    ],
)
def test_invalid_discount_or_tax_is_rejected(options):
    with pytest.raises(DocumentCalculationError):
        calculate_document(
            [{"quantity_milli": 1000, "unit_price": 100}],
            **options,
        )


@pytest.mark.parametrize(
    "items",
    [
        None,
        "invalid",
        {},
        [None],
        [{}],
        [{"quantity_milli": 1000}],
        [{"unit_price": 1000}],
    ],
)
def test_invalid_item_structure_is_rejected(items):
    with pytest.raises(DocumentCalculationError):
        calculate_document(items)


def test_line_total_overflow_is_rejected():
    with pytest.raises(DocumentCalculationError):
        calculate_line_total(MAX_INTEGER, MAX_INTEGER)


def test_subtotal_overflow_is_rejected():
    with pytest.raises(DocumentCalculationError):
        calculate_document(
            [
                {"quantity_milli": 1000, "unit_price": MAX_INTEGER},
                {"quantity_milli": 1000, "unit_price": 1},
            ]
        )


def test_grand_total_overflow_is_rejected():
    with pytest.raises(DocumentCalculationError):
        calculate_document(
            [{"quantity_milli": 1000, "unit_price": MAX_INTEGER}],
            tax_rate_bps=1,
        )


def test_maximum_integer_is_supported():
    assert calculate_line_total(1000, MAX_INTEGER) == MAX_INTEGER
    assert calculate_line_total(MAX_INTEGER, 1000) == MAX_INTEGER


def test_calculation_does_not_depend_on_global_precision():
    with localcontext() as context:
        context.prec = 4

        assert calculate_line_total(1000, MAX_INTEGER) == MAX_INTEGER


def test_input_items_are_not_modified():
    items = [
        {
            "quantity_milli": 1500,
            "unit_price": 100000,
            "name_snapshot": "Jasa Desain",
        }
    ]
    original = deepcopy(items)

    calculate_document(items)

    assert items == original


def test_discount_type_is_normalized():
    result = calculate_document(
        [{"quantity_milli": 1000, "unit_price": 100}],
        discount_type=" percent ",
        discount_value=1000,
    )

    assert result["discount_type"] == "PERCENT"
    assert result["discount_amount"] == 10


def test_integer_limit_matches_sqlite():
    assert MAX_INTEGER == 9223372036854775807


def test_price_above_sqlite_limit_is_rejected():
    with pytest.raises(DocumentCalculationError):
        calculate_line_total(1000, 9223372036854775808)