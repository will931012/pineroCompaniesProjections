"""Synthetic GDELT DOC API responses (test fixtures only).

Titles are tokenised the way GDELT returns them ("( NASDAQ : EXDV )", "$2 , 000").
"""

from datetime import UTC, datetime, timedelta
from typing import Any


def seen(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).strftime("%Y%m%dT%H%M%SZ")


def gdelt_payload() -> dict[str, Any]:
    return {
        "articles": [
            {
                "url": "https://news.example.com/exdv-results",
                "title": "Example Devices ( NASDAQ : EXDV ) fourth - quarter results beat estimates",
                "seendate": seen(30),
                "domain": "news.example.com",
                "language": "English",
                "sourcecountry": "United States",
            },
            {
                "url": "https://wire.example.org/exdv-widget",
                "title": "Example Devices to acquire Widget Co for $2 , 000 million",
                "seendate": seen(10),
                "domain": "wire.example.org",
                "language": "English",
                "sourcecountry": "United States",
            },
            {
                "url": "https://daily.example.net/widget-deal",
                "title": "Example Devices to buy Widget Co in $2 , 000 million deal",
                "seendate": seen(8),
                "domain": "daily.example.net",
                "language": "English",
                "sourcecountry": "United Kingdom",
            },
            {
                "url": "https://markets.example.com/tech-rally",
                "title": "Tech stocks rally as the Fed holds rates",
                "seendate": seen(5),
                "domain": "markets.example.com",
                "language": "English",
                "sourcecountry": "United States",
            },
            {"url": "not-a-url", "title": "Broken row", "seendate": seen(1)},
        ]
    }
