"""수집 파이프라인이 주고받는 자료 구조."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Discovered:
    source_id: str
    url: str
    lastmod: str


@dataclass
class Listing:
    source: str
    source_id: str
    url: str
    title: str
    organizer: str
    start_date: str | None
    end_date: str | None
    end_time: str | None
    participants: list[str]
    homepage: str | None
    poster_url: str | None
    image_urls: list[str]
    fields: dict[str, str]
    body_text: str

    def metadata_text(self) -> str:
        return "\n".join(f"{key}: {value}" for key, value in self.fields.items())
