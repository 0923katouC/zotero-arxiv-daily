from .base import BaseRetriever, register_retriever
import arxiv
from arxiv import Result as ArxivResult
from ..protocol import Paper
from ..utils import extract_markdown_from_pdf, extract_tex_code_from_tar
from tempfile import TemporaryDirectory
import feedparser
from urllib.request import urlretrieve, urlopen, Request
from urllib.error import URLError
from datetime import datetime, timedelta, timezone
from ..delivery_state import DeliveryState, paper_key
from tqdm import tqdm
import os
from loguru import logger

def fetch_arxiv_feed(url):
    request = Request(url, headers={"User-Agent": "zotero-arxiv-daily/1.0"})
    with urlopen(request, timeout=30) as response:
        feed = feedparser.parse(response.read())
        if feed.bozo or not feed.feed.get("title"):
            raise ValueError("arXiv RSS returned an invalid feed")
        logger.info(f"arXiv RSS: HTTP {response.status}, {len(feed.entries)} entries, "
                    f"updated={feed.feed.get('updated', 'unknown')}")
        return feed

@register_retriever("arxiv")
class ArxivRetriever(BaseRetriever):
    def __init__(self, config):
        super().__init__(config)
        if self.config.source.arxiv.category is None:
            raise ValueError("category must be specified for arxiv.")
    def _retrieve_raw_papers(self) -> list[ArxivResult]:
        client = arxiv.Client(num_retries=10,delay_seconds=10)
        query = '+'.join(self.config.source.arxiv.category)
        # Get the latest paper from arxiv rss feed
        state = DeliveryState()
        try:
            feed = fetch_arxiv_feed(f"https://rss.arxiv.org/atom/{query}")
        except (URLError, TimeoutError, ValueError) as exc:
            logger.warning(f"arXiv RSS unavailable ({type(exc).__name__}); checking query API")
            return self._retrieve_recent_papers(client, state)
        if 'Feed error for query' in feed.feed.title:
            raise Exception(f"Invalid ARXIV_QUERY: {query}.")
        raw_papers = []
        all_paper_ids = [i.id.removeprefix("oai:arXiv.org:") for i in feed.entries if i.get("arxiv_announce_type","new") == 'new']
        if not feed.entries:
            logger.warning("arXiv RSS is empty; checking query API before declaring no new papers")
            return self._retrieve_recent_papers(client, state)
        all_paper_ids = list(dict.fromkeys(i for i in all_paper_ids if not state.contains(i)))
        logger.info(f"arXiv RSS selected {len(all_paper_ids)} unseen new paper IDs")
        if self.config.executor.debug:
            all_paper_ids = all_paper_ids[:10]

        # Get full information of each paper from arxiv api
        bar = tqdm(total=len(all_paper_ids))
        for i in range(0,len(all_paper_ids),20):
            search = arxiv.Search(id_list=all_paper_ids[i:i+20])
            batch = list(client.results(search))
            missing = {paper_key(p) for p in all_paper_ids[i:i+20]} - {paper_key(p.entry_id) for p in batch}
            if missing:
                raise RuntimeError(f"arXiv metadata API omitted {len(missing)} requested papers")
            bar.update(len(batch))
            raw_papers.extend(batch)
        bar.close()

        return raw_papers

    def _retrieve_recent_papers(self, client, state):
        # Submission timestamps differ from announcement dates. A 72-hour window
        # spans RSS propagation delays and weekends; delivery state removes repeats.
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=3)
        categories = set(self.config.source.arxiv.category)
        category_query = " OR ".join(f"cat:{c}" for c in sorted(categories))
        query = f"({category_query}) AND submittedDate:[{start:%Y%m%d%H%M} TO {now:%Y%m%d%H%M}]"
        limit = max(100, self.config.executor.max_paper_num)
        search = arxiv.Search(query=query, max_results=limit,
                              sort_by=arxiv.SortCriterion.SubmittedDate,
                              sort_order=arxiv.SortOrder.Descending)
        # API failures must propagate, rather than turn into a successful empty digest.
        candidates = list(client.results(search))
        papers = [p for p in candidates if p.primary_category in categories and not state.contains(p.entry_id)]
        logger.info(f"arXiv query fallback: {len(candidates)} recent candidates, {len(papers)} unseen primary-category papers (72-hour window, limit {limit})")
        if self.config.executor.debug:
            papers = papers[:min(10, self.config.executor.max_paper_num)]
        return papers

    def convert_to_paper(self, raw_paper:ArxivResult) -> Paper:
        title = raw_paper.title
        authors = [a.name for a in raw_paper.authors]
        abstract = raw_paper.summary
        pdf_url = raw_paper.pdf_url
        full_text = extract_text_from_pdf(raw_paper)
        if full_text is None:
            full_text = extract_text_from_tar(raw_paper)
        return Paper(
            source=self.name,
            title=title,
            authors=authors,
            abstract=abstract,
            url=raw_paper.entry_id,
            pdf_url=pdf_url,
            full_text=full_text
        )

def extract_text_from_pdf(paper: ArxivResult) -> str | None:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.pdf")
        if paper.pdf_url is None:
            logger.warning(f"No PDF URL available for {paper.title}")
            return None
        urlretrieve(paper.pdf_url, path)
        try:
            full_text = extract_markdown_from_pdf(path)
        except Exception as e:
            logger.warning(f"Failed to extract full text of {paper.title} from pdf: {e}")
            full_text = None
        return full_text

def extract_text_from_tar(paper: ArxivResult) -> str | None:
    with TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "paper.tar.gz")
        source_url = paper.source_url()
        if source_url is None:
            logger.warning(f"No source URL available for {paper.title}")
            return None
        urlretrieve(source_url, path)
        try:
            file_contents = extract_tex_code_from_tar(path, paper.entry_id)
            if "all" not in file_contents:
                logger.warning(f"Failed to extract full text of {paper.title} from tar: Main tex file not found.")
                return None
            full_text = file_contents["all"]
        except Exception as e:
            logger.warning(f"Failed to extract full text of {paper.title} from tar: {e}")
            full_text = None
        return full_text
