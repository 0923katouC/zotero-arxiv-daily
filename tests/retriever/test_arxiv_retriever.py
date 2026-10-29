from zotero_arxiv_daily.retriever.arxiv_retriever import ArxivRetriever
import feedparser
import pickle

def test_arxiv_retriever(config, monkeypatch):

    parsed_result = feedparser.parse("tests/retriever/arxiv_rss_example.xml")
    monkeypatch.setattr("zotero_arxiv_daily.retriever.arxiv_retriever.fetch_arxiv_feed", lambda url: parsed_result)
    
    retriever = ArxivRetriever(config)
    papers = retriever.retrieve_papers()
    parsed_results = [i for i in parsed_result.entries if i.get("arxiv_announce_type","new") == 'new']
    assert len(papers) == len(parsed_results)
    paper_titles = [i.title for i in papers]
    parsed_titles = [i.title for i in parsed_results]
    assert set(paper_titles) == set(parsed_titles)
