"""Announcement semantics, failure reporting and delivery deduplication."""
from types import SimpleNamespace
from urllib.error import URLError

import feedparser
import pytest
from omegaconf import OmegaConf

from zotero_arxiv_daily.delivery_state import DeliveryState
from zotero_arxiv_daily.retriever import arxiv_retriever as module


def make_retriever(monkeypatch, tmp_path, entries=()):
    monkeypatch.setenv("DELIVERY_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.delenv("ARXIV_DIAGNOSTIC", raising=False)
    feed = feedparser.FeedParserDict(feed=feedparser.FeedParserDict(title="gr-qc updates"), entries=list(entries))
    monkeypatch.setattr(module, "fetch_arxiv_feed", lambda url: feed)
    monkeypatch.setattr(module, "sleep", lambda _: None)
    def api_must_not_be_used(**kwargs):
        raise AssertionError("Production retrieval must not query old submissions")
    monkeypatch.setattr(module.arxiv, "Client", api_must_not_be_used)
    config = OmegaConf.create({"source": {"arxiv": {"category": ["gr-qc"]}}, "executor": {"debug": False, "max_paper_num": 100}})
    return module.ArxivRetriever(config)


def test_deferred_announcement_does_not_turn_recent_papers_into_new_batch(monkeypatch, tmp_path):
    retriever = make_retriever(monkeypatch, tmp_path)
    assert retriever._retrieve_raw_papers() == []


def test_rss_metadata_does_not_need_query_api(monkeypatch, tmp_path):
    entry = feedparser.FeedParserDict(id="oai:arXiv.org:2610.12345v1", title="New paper", summary="Abstract: Original result", author="Test Author", arxiv_announce_type="new")
    retriever = make_retriever(monkeypatch, tmp_path, entries=[entry])
    papers = retriever._retrieve_raw_papers()
    assert len(papers) == 1
    assert papers[0].summary == "Original result"


def test_successfully_delivered_versions_are_not_sent_again(monkeypatch, tmp_path):
    entry = feedparser.FeedParserDict(id="oai:arXiv.org:2610.12345v2", title="Already emailed", arxiv_announce_type="new")
    retriever = make_retriever(monkeypatch, tmp_path, entries=[entry])
    DeliveryState(tmp_path / "state.json").mark_sent(["https://arxiv.org/abs/2610.12345v1"])
    assert retriever._retrieve_raw_papers() == []


def test_network_failure_is_not_reported_as_no_announcement(monkeypatch, tmp_path):
    retriever = make_retriever(monkeypatch, tmp_path)
    attempts = []
    def fail(url):
        attempts.append(url)
        raise URLError("unavailable")
    monkeypatch.setattr(module, "fetch_arxiv_feed", fail)
    with pytest.raises(RuntimeError, match="could not be fetched"):
        retriever._retrieve_raw_papers()
    assert len(attempts) == 5


def test_invalid_http_feed_retries_and_fails(monkeypatch, tmp_path):
    retriever = make_retriever(monkeypatch, tmp_path)
    feed = SimpleNamespace(status=503, bozo=False, feed=SimpleNamespace(title="Unavailable"), entries=[])
    monkeypatch.setattr(module, "fetch_arxiv_feed", lambda url: feed)
    with pytest.raises(RuntimeError, match="remained invalid"):
        retriever._retrieve_raw_papers()
