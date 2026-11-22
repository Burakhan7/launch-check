"""Chrome UX Report (CrUX) API: gerçek Chrome kullanıcılarının son 28 günlük p75 değerleri.

Ücretsiz kota (`LC_GOOGLE_API_KEY`). Google'a sadece sitenin adresi gönderilir. Anahtar URL'ye
değil `X-Goog-Api-Key` başlığına yazılır: httpx istek URL'lerini logladığı için anahtar loga
düşmez. Anahtar yoksa, kota dolmuşsa, servis hata verirse veya site CrUX'ta yoksa modül saha
verisi olmadan devam eder (durum `FieldStatus`).
"""

import logging
from typing import Any

import httpx

from launchcheck.deep.perf_data import FieldData, FieldStatus

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-Goog-Api-Key"
METRICS = (
    "largest_contentful_paint",
    "cumulative_layout_shift",
    "interaction_to_next_paint",
    "first_contentful_paint",
)


class CruxError(Exception):
    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


def _p75(record: dict[str, Any], metric: str) -> float | None:
    value = ((record.get("metrics") or {}).get(metric) or {}).get("percentiles", {}).get("p75")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_record(body: dict[str, Any], scope: str, form_factor: str) -> FieldData | None:
    record = body.get("record") or {}
    data = FieldData(
        scope=scope,
        form_factor=form_factor,
        lcp_ms=_p75(record, "largest_contentful_paint"),
        cls=_p75(record, "cumulative_layout_shift"),
        inp_ms=_p75(record, "interaction_to_next_paint"),
        fcp_ms=_p75(record, "first_contentful_paint"),
        collection_period=record.get("collectionPeriod"),
    )
    if data.lcp_ms is None and data.cls is None and data.inp_ms is None:
        return None
    return data


class CruxClient:
    def __init__(
        self,
        api_key: str,
        endpoint: str,
        timeout_s: float,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._key = api_key
        self._endpoint = endpoint
        self._timeout = timeout_s
        self._client = client

    async def _query(self, body: dict[str, Any]) -> dict[str, Any] | None:
        """404 → None (CrUX'ta bu adres/origin için yeterli veri yok)."""
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        try:
            res = await client.post(
                self._endpoint,
                json={**body, "metrics": list(METRICS)},
                headers={API_KEY_HEADER: self._key},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.info("CrUX isteği başarısız (%s)", type(exc).__name__)
            raise CruxError(FieldStatus.ERROR) from exc
        finally:
            if self._client is None:
                await client.aclose()
        if res.status_code == 404:
            return None
        if res.status_code == 429:
            raise CruxError(FieldStatus.QUOTA)
        if res.status_code != 200:
            logger.info("CrUX yanıtı %s", res.status_code)
            raise CruxError(FieldStatus.ERROR)
        try:
            payload = res.json()
        except ValueError as exc:
            raise CruxError(FieldStatus.ERROR) from exc
        return payload if isinstance(payload, dict) else None

    async def field_data(self, page_url: str, origin: str) -> tuple[str, FieldData | None]:
        """Önce sayfa (telefon), sonra origin (telefon), sonra origin (tüm cihazlar)."""
        if not self._key:
            return FieldStatus.NO_KEY, None
        attempts = (
            ({"url": page_url, "formFactor": "PHONE"}, "url", "phone"),
            ({"origin": origin, "formFactor": "PHONE"}, "origin", "phone"),
            ({"origin": origin}, "origin", "all"),
        )
        try:
            for body, scope, form_factor in attempts:
                payload = await self._query(body)
                data = parse_record(payload, scope, form_factor) if payload else None
                if data is not None:
                    return FieldStatus.OK, data
        except CruxError as exc:
            return exc.status, None
        return FieldStatus.NO_DATA, None
