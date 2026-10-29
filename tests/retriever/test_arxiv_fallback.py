from types import SimpleNamespace
from urllib.error import URLError
import feedparser
import pytest
from omegaconf import OmegaConf
from zotero_arxiv_daily.delivery_state import DeliveryState
from zotero_arxiv_daily.retriever import arxiv_retriever as module


def make_retriever(monkeypatch, tmp_path, entries=(), candidates=()):
    monkeypatch.setenv("DELIVERY_STATE_FILE", str(tmp_path / "state.json"))
    feed = feedparser.FeedParserDict(feed=feedparser.FeedParserDict(title="gr-qc updates"), entries=list(entries))
    monkeypatch.setattr(module, "fetch_arxiv_feed", lambda url: feed)
    calls = []
    class Client:
        def results(self, search):
            calls.append(search)
            return iter(candidates)
    monkeypatch.setattr(module.arxiv, "Client", lambda **kwargs: Client())
    config = OmegaConf.create({"source": {"arxiv": {"category": ["gr-qc"]}}, "executor": {"debug": False, "max_paper_num": 100}})
    return module.ArxivRetriever(config), calls


def test_empty_feed_checks_api_and_excludes_sent_and_cross_lists(monkeypatch, tmp_path):
    candidates = [SimpleNamespace(entry_id=f"http://arxiv.org/abs/2610.1234{i}v1", primary_category=category)
                  for i, category in enumerate(["gr-qc", "gr-qc", "hep-th"])]
    retriever, calls = make_retriever(monkeypatch, tmp_path, candidates=candidates)
    state = DeliveryState(tmp_path / "state.json")
    state.mark_sent([candidates[0].entry_id])
    assert retriever._retrieve_raw_papers() == [candidates[1]]
    assert "submittedDate:[" in calls[0].query
    assert "cat:gr-qc" in calls[0].query


def test_empty_feed_and_empty_api_are_legitimately_empty(monkeypatch, tmp_path):
    retriever, calls = make_retriever(monkeypatch, tmp_path)
    assert retriever._retrieve_raw_papers() == []
    assert len(calls) == 1


def test_rss_network_failure_uses_api(monkeypatch, tmp_path):
    paper = SimpleNamespace(entry_id="http://arxiv.org/abs/2610.12345v1", primary_category="gr-qc")
    retriever, _ = make_retriever(monkeypatch, tmp_path, candidates=[paper])
    def fail(url):
        raise URLError("feed unavailable")
    monkeypatch.setattr(module, "fetch_arxiv_feed", fail)
    assert retriever._retrieve_raw_papers() == [paper]


def test_api_failure_is_not_reported_as_no_new_papers(monkeypatch, tmp_path):
    retriever, _ = make_retriever(monkeypatch, tmp_path)
    def fail(client, state):
        raise RuntimeError("metadata API unavailable")
    monkeypatch.setattr(retriever, "_retrieve_recent_papers", fail)
    with pytest.raises(RuntimeError, match="API unavailable"):
        retriever._retrieve_raw_papers()


def test_incomplete_metadata_response_fails(monkeypatch, tmp_path):
    entry = feedparser.FeedParserDict(id="oai:arXiv.org:2610.12345", arxiv_announce_type="new")
    retriever, _ = make_retriever(monkeypatch, tmp_path, entries=[entry])
    with pytest.raises(RuntimeError, match="omitted 1"):
        retriever._retrieve_raw_papers()
