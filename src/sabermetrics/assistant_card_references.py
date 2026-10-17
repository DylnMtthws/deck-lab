"""Conservative spelling normalization; distinct Oracle identities stay distinct."""

import re
import unicodedata


def reference_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value.casefold()).replace(
        "judgement", "judgment"
    )
    return re.sub(r"[^a-z0-9]", "", text)
