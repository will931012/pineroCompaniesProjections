"""Link news headlines to companies, recording how each link was made."""

import re
from dataclasses import dataclass

# Legal-form suffixes that headlines leave out ("Apple Inc." → "Apple").
_SUFFIXES = re.compile(
    r"[,.]?\s+(&\s*)?(inc|incorporated|corp|corporation|co|com|company|ltd|limited|plc|llc|lp|l\.p|n\.v|"
    r"s\.a|ag|se|holdings?|group|/de|/md|/new)\.?$",
    re.I,
)
_EXCHANGE_TICKER = r"\((?:(?:nyse|nasdaq|nyse american|amex|otc)\s*:\s*)?{ticker}\)"


@dataclass(frozen=True)
class Mention:
    method: str  # title_ticker | title_name | query_only
    confidence: str  # high | low


def short_name(legal_name: str) -> str:
    """Name as headlines usually write it: legal suffixes removed, all-caps names title-cased."""
    name = " ".join(legal_name.replace("&amp;", "&").split())
    previous = None
    while previous != name:
        previous = name
        name = _SUFFIXES.sub("", name).strip(" ,.")
    if name.isupper():
        name = " ".join(w if len(w) <= 3 else w.capitalize() for w in name.split())
    return name


def search_query(legal_name: str, ticker: str) -> str:
    """GDELT query: the company's short name as a phrase, or its ticker when distinctive."""
    terms = [f'"{short_name(legal_name)}"']
    if len(ticker) >= 3 and ticker.isalpha():
        terms.append(f'"{ticker} stock"')
    return f"({' OR '.join(terms)})" if len(terms) > 1 else terms[0]


def mention(title: str, legal_name: str, tickers: list[str]) -> Mention:
    for ticker in tickers:
        exchange = re.compile(_EXCHANGE_TICKER.format(ticker=re.escape(ticker)), re.I)
        cashtag = re.compile(rf"\${re.escape(ticker)}\b")
        bare = re.compile(rf"\b{re.escape(ticker)}\b")
        # A bare ticker counts only when it is distinctive (3+ letters, written in capitals).
        if (
            exchange.search(title)
            or cashtag.search(title)
            or (len(ticker) >= 3 and bare.search(title))
        ):
            return Mention("title_ticker", "high")
    name = short_name(legal_name)
    if name and re.search(rf"\b{re.escape(name)}(?:'s)?\b", title, re.I):
        return Mention("title_name", "high")
    return Mention("query_only", "low")
