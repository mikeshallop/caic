import app as app_module


def test_sanitize_outbound_url_allows_http_https():
    assert app_module.sanitize_outbound_url("https://example.com/path") == "https://example.com/path"
    assert app_module.sanitize_outbound_url("http://example.com") == "http://example.com"


def test_sanitize_outbound_url_blocks_unsafe_schemes():
    assert app_module.sanitize_outbound_url("javascript:alert(1)") == ""
    assert app_module.sanitize_outbound_url("data:text/html,evil") == ""
    assert app_module.sanitize_outbound_url("file:///etc/passwd") == ""


def test_sanitize_outbound_url_blocks_relative_and_empty():
    assert app_module.sanitize_outbound_url("/relative/path") == ""
    assert app_module.sanitize_outbound_url("") == ""
