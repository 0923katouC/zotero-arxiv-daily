"""Announcement semantics, failure reporting and delivery deduplication."""
from datetime import datetime, timedelta, timezone
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


NEW_ENTRY = feedparser.FeedParserDict(
    id="oai:arXiv.org:2610.12345v1",
    title="New paper",
    summary="Abstract: Original result",
    author="Test Author",
    arxiv_announce_type="new",
)


def atom_feed(entries=(), generated_at=None):
    """FeedParserDict shaped like a real rss.arxiv.org/atom response."""
    meta = feedparser.FeedParserDict(title="gr-qc updates")
    if generated_at is not None:
        meta["updated"] = generated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        meta["updated_parsed"] = generated_at.utctimetuple()
    return feedparser.FeedParserDict(feed=meta, entries=list(entries), status=200)


def test_cached_empty_feed_is_refetched_without_the_cdn(monkeypatch, tmp_path):
    """A cached empty feed must not be reported as "no announcement"."""
    retriever = make_retriever(monkeypatch, tmp_path)
    calls = []

    def fetch(url):
        calls.append(url)
        return atom_feed([NEW_ENTRY]) if "cb=" in url else atom_feed()

    monkeypatch.setattr(module, "fetch_arxiv_feed", fetch)
    papers = retriever._retrieve_raw_papers()

    assert len(papers) == 1
    assert len(calls) == 2
    assert "rss.arxiv.org/atom/gr-qc" in calls[0]
    assert "cb=" in calls[1]


def test_hours_old_cached_feed_is_refetched(monkeypatch, tmp_path):
    retriever = make_retriever(monkeypatch, tmp_path)
    generated_at = datetime.now(timezone.utc) - timedelta(hours=10)
    calls = []

    def fetch(url):
        calls.append(url)
        if "cb=" in url:
            return atom_feed([NEW_ENTRY], generated_at=datetime.now(timezone.utc))
        return atom_feed([], generated_at=generated_at)

    monkeypatch.setattr(module, "fetch_arxiv_feed", fetch)
    papers = retriever._retrieve_raw_papers()

    assert len(papers) == 1
    assert "cb=" in calls[1]


def test_fresh_populated_feed_is_used_as_is(monkeypatch, tmp_path):
    retriever = make_retriever(monkeypatch, tmp_path)
    calls = []
    fresh = atom_feed([NEW_ENTRY], generated_at=datetime.now(timezone.utc))

    def fetch(url):
        calls.append(url)
        return fresh

    monkeypatch.setattr(module, "fetch_arxiv_feed", fetch)
    assert len(retriever._retrieve_raw_papers()) == 1
    assert len(calls) == 1


def test_genuinely_empty_feed_returns_no_papers(monkeypatch, tmp_path):
    retriever = make_retriever(monkeypatch, tmp_path)
    calls = []

    def fetch(url):
        calls.append(url)
        return atom_feed()

    monkeypatch.setattr(module, "fetch_arxiv_feed", fetch)
    assert retriever._retrieve_raw_papers() == []
    assert len(calls) == 2
