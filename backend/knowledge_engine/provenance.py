"""Public source links declared explicitly in document headers."""

import re
from urllib.parse import urlsplit


def source_urls(texts: list[str]) -> list[str]:
    """Return distinct HTTPS links from complete ``Source:`` header lines only."""
    found: list[str] = []
    for text in texts:
        for match in re.finditer(r"^Source:[ \t]*(https://[^\s]+)[ \t]*$", text, re.MULTILINE):
            url = match.group(1)
            try:
                parsed = urlsplit(url)
                hostname = parsed.hostname
                if (
                    parsed.scheme != "https"
                    or not hostname
                    or parsed.username is not None
                    or parsed.password is not None
                    or any(ord(c) < 32 for c in url)
                ):
                    continue
                hostname.encode("idna")
                _ = parsed.port  # Validate malformed port declarations.
            except (ValueError, UnicodeError):
                continue
            if url not in found:
                found.append(url)
    return found
