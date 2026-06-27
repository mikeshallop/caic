from search import sanitize_outbound_url


def test_sanitize_outbound_url_allows_http_https():
    assert sanitize_outbound_url("https://example.com/path") == "https://example.com/path"
    assert sanitize_outbound_url("http://example.com") == "http://example.com"


def test_sanitize_outbound_url_blocks_unsafe_schemes():
    assert sanitize_outbound_url("javascript:alert(1)") == ""
    assert sanitize_outbound_url("data:text/html,evil") == ""
    assert sanitize_outbound_url("file:///etc/passwd") == ""


def test_sanitize_outbound_url_blocks_relative_and_empty():
    assert sanitize_outbound_url("/relative/path") == ""
    assert sanitize_outbound_url("") == ""
