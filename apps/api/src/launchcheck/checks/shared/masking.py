"""Secret maskeleme. Bulunan secret'lar sadece maskeli hâliyle saklanır ve gösterilir."""

MASK_PREFIX_LEN = 6
MASK_SUFFIX_LEN = 4
# Bundan kısa değerlerde 6+4 karakter göstermek secret'ın çoğunu açığa çıkarır.
MASK_MIN_LEN_FOR_FULL_FORMAT = 20
MASK_SHORT_PREFIX_LEN = 2
MASK_FILLER = "…"


def mask_secret(value: str) -> str:
    """İlk 6 + son 4 karakteri bırakır. Kısa değerlerde sadece ilk 2 karakter kalır."""
    if len(value) < MASK_MIN_LEN_FOR_FULL_FORMAT:
        return value[:MASK_SHORT_PREFIX_LEN] + MASK_FILLER
    return value[:MASK_PREFIX_LEN] + MASK_FILLER + value[-MASK_SUFFIX_LEN:]
