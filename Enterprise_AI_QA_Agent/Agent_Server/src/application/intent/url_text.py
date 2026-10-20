from __future__ import annotations

import re


# Chinese sentence punctuation is a URL boundary even when the user does not
# put whitespace between the URL and the following clause. Keep this pattern
# shared by intent and mode-intent parsing because it also feeds authorization
# target matching.
HTTP_URL_PATTERN = re.compile(
    r"https?://[^\s<>'\"，。、；：！？…]+",
    flags=re.IGNORECASE,
)
