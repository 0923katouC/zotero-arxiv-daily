import json
import pytest
from zotero_arxiv_daily.delivery_state import DeliveryState, paper_key


def test_arxiv_versions_and_urls_have_one_identity():
    assert paper_key("http://arxiv.org/abs/2610.12345v1") == paper_key("https://arxiv.org/pdf/2610.12345v2.pdf")
    assert paper_key("oai:arXiv.org:2610.12345v1") == paper_key("2610.12345")
    assert paper_key("https://example.org/paper") == "https://example.org/paper"


def test_only_explicitly_confirmed_delivery_is_persisted(tmp_path):
    path = tmp_path / "state.json"
    state = DeliveryState(path)
    url = "https://arxiv.org/abs/2610.12345v1"
    assert not state.contains(url)
    assert not path.exists()
    state.mark_sent([url])
    assert DeliveryState(path).contains("http://arxiv.org/abs/2610.12345v2")
    assert not DeliveryState(path).contains("http://arxiv.org/abs/2610.54321v1")
    assert list(json.loads(path.read_text())["sent"]) == ["arxiv:2610.12345"]


def test_invalid_delivery_history_is_not_silently_reset(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"version": 2}')
    with pytest.raises(ValueError):
        DeliveryState(path)
