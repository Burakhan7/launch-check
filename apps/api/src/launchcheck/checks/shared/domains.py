"""Kayıtlı alan adı hesabı (ör. uzem.site.org.tr → site.org.tr).

tldextract ağdan Public Suffix List indirmez (`suffix_list_urls=()`), paketle gelen
snapshot'ı kullanır; önbellek diske yazılmaz (`cache_dir=None`, salt okunur FS uyumlu).
"""

from functools import lru_cache

import tldextract

_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


@lru_cache(maxsize=4096)
def registered_domain(host: str) -> str:
    """Kayıtlı alan adı; IP, localhost gibi durumlarda host'un kendisi."""
    host = host.lower().rstrip(".")
    return _EXTRACT(host).top_domain_under_public_suffix or host


def same_site(host_a: str, host_b: str) -> bool:
    return bool(host_a) and registered_domain(host_a) == registered_domain(host_b)
