import pytest

from quantview.pipeline.daily import classify_run_status


@pytest.mark.parametrize(
    ("rows_accepted", "products_found", "expected", "status"),
    [
        (0, 0, 3, "failed"),
        (100, 2, 3, "partial"),
        (0, 3, 3, "partial"),
        (100, 3, 3, "success"),
    ],
)
def test_classify_run_status(rows_accepted, products_found, expected, status):
    assert classify_run_status(rows_accepted, products_found, expected) == status


def test_product_presence_does_not_override_contract_gaps():
    assert classify_run_status(100, 3, 3, complete_products=2) == "partial"
