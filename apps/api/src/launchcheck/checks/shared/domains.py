"""Kayıtlı alan adı hesabı (ör. uzem.site.org.tr → site.org.tr).

tldextract ağdan Public Suffix List indirmez (`suffix_list_urls=()`), paketle gelen
snapshot'ı kullanır; önbellek diske yazılmaz (`cache_dir=None`, salt okunur FS uyumlu).
"""

from functools import lru_cache

import tldextract

_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)
# PSL'nin "private" bölümü dahil (github.io, vercel.app, pages.dev gibi ortak barındırmalar)
_EXTRACT_PRIVATE = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True
)


@lru_cache(maxsize=4096)
def registered_domain(host: str) -> str:
    """Kayıtlı alan adı; IP, localhost gibi durumlarda host'un kendisi."""
    host = host.lower().rstrip(".")
    return _EXTRACT(host).top_domain_under_public_suffix or host


def same_site(host_a: str, host_b: str) -> bool:
    return bool(host_a) and registered_domain(host_a) == registered_domain(host_b)


def is_shared_hosting(host: str, extra_suffixes: list[str] | tuple[str, ...] = ()) -> bool:
    """Host ortak bir barındırma alan adının altında mı (ör. app.vercel.app, x.lovable.app)?
    Öyleyse kayıtlı alan adı, DNS'i ve diğer alt alan adları platforma aittir."""
    host = host.lower().rstrip(".")
    if any(host == s or host.endswith("." + s) for s in extra_suffixes):
        return True
    return bool(_EXTRACT_PRIVATE(host).is_private)
