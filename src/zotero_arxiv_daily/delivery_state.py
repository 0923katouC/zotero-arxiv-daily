"""Remember successfully emailed papers; never store credentials or paper text."""
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


def paper_key(url: str) -> str:
    value = re.sub(r"^https?://(?:www\.|export\.)?arxiv\.org/(?:abs|pdf)/", "", url)
    value = value.removeprefix("oai:arXiv.org:").removesuffix(".pdf")
    if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?", value):
        return "arxiv:" + re.sub(r"v\d+$", "", value)
    return url


class DeliveryState:
    def __init__(self, path=None):
        path = path or os.environ.get("DELIVERY_STATE_FILE")
        self.path = Path(path) if path else None
        self.sent = {}
        if self.path and self.path.exists():
            data = json.loads(self.path.read_text())
            if data.get("version") != 1 or not isinstance(data.get("sent"), dict):
                raise ValueError("Invalid paper delivery state")
            self.sent = data["sent"]
            for key, date in self.sent.items():
                if not isinstance(key, str) or not isinstance(date, str):
                    raise ValueError("Invalid paper delivery state entry")
                if datetime.fromisoformat(date).tzinfo is None:
                    raise ValueError("Delivery timestamps must include a timezone")

    def contains(self, url: str) -> bool:
        return paper_key(url) in self.sent

    def mark_sent(self, urls):
        urls = list(urls)
        if self.path is None or not urls:
            return
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=30)
        self.sent = {key: date for key, date in self.sent.items()
                     if datetime.fromisoformat(date) >= cutoff}
        self.sent.update({paper_key(url): now.isoformat() for url in urls})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "sent": self.sent}, sort_keys=True))
        temporary.replace(self.path)
