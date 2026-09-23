import json
import pytest
from bs4 import BeautifulSoup
from assistant.tools.WebAccess import WebAccess


def _make_html_with_next_data(data: dict) -> str:
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script><p>visible</p></body></html>'


@pytest.mark.unit
class TestExtractNextDataSummary:
    def test_no_script_tag_returns_empty(self):
        soup = BeautifulSoup("<html><body><p>hi</p></body></html>", "html.parser")
        assert WebAccess._extract_next_data_summary(soup) == ""

    def test_invalid_json_returns_empty(self):
        soup = BeautifulSoup('<html><body><script id="__NEXT_DATA__">not json{</script></body></html>', "html.parser")
        assert WebAccess._extract_next_data_summary(soup) == ""

    def test_no_product_in_data_returns_empty(self):
        data = {"props": {"pageProps": {"title": "About Us"}}}
        soup = BeautifulSoup(_make_html_with_next_data(data), "html.parser")
        assert WebAccess._extract_next_data_summary(soup) == ""

    def test_finds_product_at_pageProps_product_product(self):
        data = {"props": {"pageProps": {"product": {"product": {
            "name": "T-Shirt", "code": "ABC123", "skus": [{"size": "M", "inStock": True}]
        }}}}}
        soup = BeautifulSoup(_make_html_with_next_data(data), "html.parser")
        result = WebAccess._extract_next_data_summary(soup)
        assert "T-Shirt" in result
        assert "ABC123" in result

    def test_finds_product_at_pageProps_product(self):
        data = {"props": {"pageProps": {"product": {
            "name": "Jacket", "variants": [{"size": "L", "inStock": False}]
        }}}}
        soup = BeautifulSoup(_make_html_with_next_data(data), "html.parser")
        result = WebAccess._extract_next_data_summary(soup)
        assert "Jacket" in result

    def test_finds_product_at_initialProduct(self):
        data = {"props": {"pageProps": {"initialProduct": {
            "name": "Sneakers", "skus": [{"size": "42", "inStock": True}]
        }}}}
        soup = BeautifulSoup(_make_html_with_next_data(data), "html.parser")
        result = WebAccess._extract_next_data_summary(soup)
        assert "Sneakers" in result

    def test_finds_product_at_pdpData_product(self):
        data = {"props": {"pageProps": {"pdpData": {"product": {
            "name": "Dress", "skus": [{"size": "S", "inStock": True}]
        }}}}}
        soup = BeautifulSoup(_make_html_with_next_data(data), "html.parser")
        result = WebAccess._extract_next_data_summary(soup)
        assert "Dress" in result

    def test_dict_without_skus_variants_or_name_returns_empty(self):
        data = {"props": {"pageProps": {"product": {"product": {"someOtherField": 123}}}}}
        soup = BeautifulSoup(_make_html_with_next_data(data), "html.parser")
        assert WebAccess._extract_next_data_summary(soup) == ""


@pytest.mark.unit
class TestFormatNextDataProduct:
    def test_full_product_with_skus(self):
        product = {
            "name": "Cotton Tee",
            "code": "CT001",
            "currentPrice": 199,
            "oldPrice": 299,
            "onlineStockStatus": "IN_STOCK",
            "inStock": True,
            "skus": [
                {"size": "S", "code": "CT001-S", "onlineStockStatus": "IN_STOCK", "inStock": True, "isLowStock": False},
                {"size": "M", "code": "CT001-M", "onlineStockStatus": "OUT_OF_STOCK", "inStock": False, "isLowStock": False},
                {"size": "L", "code": "CT001-L", "onlineStockStatus": "IN_STOCK", "inStock": True, "isLowStock": True},
            ]
        }
        result = WebAccess._format_next_data_product(product)
        assert "[Structured product data from __NEXT_DATA__]" in result
        assert "Product: Cotton Tee (code CT001)" in result
        assert "Price: 199 (was 299)" in result
        assert "Overall stock: IN_STOCK, inStock=true" in result
        assert "Per-size stock:" in result
        assert "size=S" in result
        assert "sku=CT001-S" in result
        assert "size=M" in result
        assert "inStock=false" in result
        assert "size=L" in result
        assert "lowStock=true" in result

    def test_product_name_without_code(self):
        product = {"name": "Plain Shirt", "skus": [{"size": "M", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "Product: Plain Shirt" in result
        assert "code" not in result

    def test_product_code_only(self):
        product = {"code": "XYZ", "skus": [{"size": "L", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "Product code: XYZ" in result

    def test_product_with_id_instead_of_code(self):
        product = {"name": "Hat", "id": "hat-42", "skus": [{"size": "One Size", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "hat-42" in result

    def test_sale_price_fallback(self):
        product = {"name": "X", "salePrice": 149, "skus": [{"size": "M", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "Price: 149" in result

    def test_list_price_fallback(self):
        product = {"name": "X", "listPrice": 399, "skus": [{"size": "M", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "Price: 399" in result

    def test_no_old_price_shown_when_same_as_current(self):
        product = {"name": "X", "currentPrice": 100, "oldPrice": 100, "skus": [{"size": "M", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "was" not in result

    def test_stock_status_via_stockStatus_field(self):
        product = {"name": "X", "stockStatus": "LOW_STOCK", "skus": [{"size": "M", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "LOW_STOCK" in result

    def test_variants_instead_of_skus(self):
        product = {"name": "Pants", "variants": [
            {"size": "30", "stockStatus": "IN_STOCK", "inStock": True},
            {"size": "32", "stockStatus": "SOLD_OUT", "inStock": False},
        ]}
        result = WebAccess._format_next_data_product(product)
        assert "size=30" in result
        assert "size=32" in result
        assert "inStock=true" in result
        assert "inStock=false" in result

    def test_sku_uses_name_when_no_size(self):
        product = {"name": "X", "skus": [{"name": "Small", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "size=Small" in result

    def test_sku_uses_label_as_last_resort(self):
        product = {"name": "X", "skus": [{"label": "XL", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "size=XL" in result

    def test_non_dict_skus_skipped(self):
        product = {"name": "X", "skus": ["bad", None, {"size": "M", "inStock": True}]}
        result = WebAccess._format_next_data_product(product)
        assert "size=M" in result
        lines = [l for l in result.split("\n") if l.startswith("  - ")]
        assert len(lines) == 1

    def test_empty_product_returns_empty(self):
        result = WebAccess._format_next_data_product({})
        assert result == ""


@pytest.mark.unit
class TestNextDataIntegrationWithExtractText:
    def test_next_data_appended_to_extracted_text(self):
        data = {"props": {"pageProps": {"product": {"product": {
            "name": "Hoodie", "code": "H01",
            "currentPrice": 499,
            "skus": [{"size": "M", "onlineStockStatus": "IN_STOCK", "inStock": True}]
        }}}}}
        html = _make_html_with_next_data(data)
        result = WebAccess.extract_text_with_links(html, "https://shop.example.com")
        assert "visible" in result
        assert "Hoodie" in result
        assert "size=M" in result
