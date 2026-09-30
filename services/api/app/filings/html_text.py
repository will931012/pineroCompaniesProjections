"""Plain-text paragraphs from an EDGAR document (HTML, inline XBRL, or plain text).

Block elements become paragraph breaks, table cells are joined with " | " so a row stays on
one line, and hidden content (inline-XBRL headers, `display:none`) is dropped. Page numbers
and repeated "Table of Contents" links are removed. Nothing is paraphrased or reordered.
"""

import re

from lxml import etree, html

BLOCK_TAGS = frozenset(
    {
        "address", "article", "blockquote", "br", "center", "dd", "div", "dl", "dt", "h1",
        "h2", "h3", "h4", "h5", "h6", "hr", "li", "ol", "p", "pre", "section", "table",
        "tbody", "thead", "tfoot", "tr", "ul",
    }
)  # fmt: skip
CELL_TAGS = frozenset({"td", "th"})
DROP_TAGS = frozenset({"script", "style", "head", "title", "noscript", "ix:header"})

_SPACES = re.compile(r"[ \t\r\f\v  -​  　]+")
_EMPTY_CELLS = re.compile(r"(\s*\|\s*)+")
_PAGE_NUMBER = re.compile(
    r"^(?:(?:page\s+)?[-–—]?\s*\d{1,3}\s*[-–—]?|(?=[ivx])x{0,3}(?:ix|iv|v?i{0,3}))$", re.IGNORECASE
)
_TOC_LINK = re.compile(r"^(back to |return to )?table of contents$", re.IGNORECASE)


def _hidden(element: etree._Element) -> bool:
    style = (element.get("style") or "").replace(" ", "").lower()
    return "display:none" in style


def _clean_line(line: str) -> str:
    line = _SPACES.sub(" ", line)
    line = _EMPTY_CELLS.sub(" | ", line).strip(" |")
    return line.strip()


def paragraphs_from_html(content: bytes) -> list[str]:
    try:
        root = html.fromstring(content)
    except (etree.ParserError, ValueError):
        return []
    for element in list(root.iter()):
        tag = element.tag if isinstance(element.tag, str) else ""
        if tag.lower() in DROP_TAGS or (tag and _hidden(element)):
            element.drop_tree()
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        tag = element.tag.lower()
        if tag in BLOCK_TAGS:
            element.text = "\n" + (element.text or "")
            element.tail = "\n" + (element.tail or "")
        elif tag in CELL_TAGS:
            element.tail = " | " + (element.tail or "")
    lines = (_clean_line(line) for line in root.text_content().split("\n"))
    return [
        line
        for line in lines
        if line and not _PAGE_NUMBER.match(line) and not _TOC_LINK.match(line)
    ]


def paragraphs_from_text(content: bytes) -> list[str]:
    """Plain-text filings: paragraphs are separated by blank lines."""
    text = content.decode("utf-8", errors="replace").replace("\r\n", "\n")
    blocks = re.split(r"\n\s*\n", text)
    result = []
    for block in blocks:
        line = _clean_line(" ".join(block.split("\n")))
        if line and not _PAGE_NUMBER.match(line) and not _TOC_LINK.match(line):
            result.append(line)
    return result


def document_paragraphs(content: bytes, document_name: str) -> list[str]:
    if document_name.lower().endswith((".htm", ".html", ".xhtml")):
        return paragraphs_from_html(content)
    return paragraphs_from_text(content)
