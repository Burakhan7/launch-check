"""Dış görünürlük modülü (ağ şeridi, PHASE2 §6.3).

Beş bağımsız parça paralel çalışır; biri başarısız olursa diğerleri etkilenmez ve sadece o
parçanın check'leri atlanır (raporda "geçti" sayılmaz):
- subdomains: crt.sh → DNS → canlı alt alan adlarında seçili Faz 1 check'leri + ele geçirme riski
- email: MX / SPF / DMARC
- wayback: arşivdeki hassas yollar, şimdi hâlâ açık mı
- rdap: alan adı bitiş tarihi
- cert: TLS sertifikası bitiş tarihi

Hedef ortak barındırmadaysa (ör. app.vercel.app) alan adı platformundur: sadece wayback çalışır.
Pasiflik: alt alan adlarına ve arşiv yollarına sadece normal ziyaretçi GET'i gider; form, API
çağrısı, bulunan anahtarın kullanımı veya servis "claim" etme yoktur.
"""

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from launchcheck.checks.base import Check, TargetType
from launchcheck.checks.registry import load_builtin_checks, run_checks
from launchcheck.checks.shared.domains import is_shared_hosting, registered_domain
from launchcheck.collectors.base import CollectorError
from launchcheck.collectors.fetcher import FetchError, SafeFetcher
from launchcheck.collectors.web_collector import WebCollector, WebContext
from launchcheck.config import Settings, get_settings
from launchcheck.deep.context import DeepContext
from launchcheck.deep.external.dns import DnsClient, DnsError, SystemDns, addresses, cname_chain
from launchcheck.deep.external.email_dns import collect_email
from launchcheck.deep.external.rdap import fetch_cert, parse_expiry, rdap_url
from launchcheck.deep.external.services import (
    RedisServiceLimiter,
    ServiceClient,
    ServiceError,
    ServiceLimiter,
)
from launchcheck.deep.external.subdomains import (
    certspotter_url,
    crtsh_url,
    parse_certspotter,
    parse_names,
    select_candidates,
    twins,
)
from launchcheck.deep.external.takeover import assess
from launchcheck.deep.external.wayback import cdx_url, parse_rows, probe_paths, sensitive_paths
from launchcheck.deep.external_data import (
    CertInfo,
    DomainExpiry,
    ExternalData,
    PartStatus,
    SubdomainScan,
    SubFinding,
)
from launchcheck.deep.modules import DeepModule, Lane, ModuleOutput
from launchcheck.security.ssrf import UrlGuard, is_public_ip, parse_ip
from launchcheck.security.url import TargetValidationError

logger = logging.getLogger(__name__)

CHECK_BUDGET_S = 10  # check'ler ve kapanış için ayrılan süre
SCAN_MARGIN_S = 3  # alt alan adı taramaları parça süresi dolmadan bu kadar önce kesilir
HOME_BODY_BYTES = 256 * 1024  # ele geçirme parmak izi için ana sayfadan okunan kısım
SHARED_HOST_PARTS = ("subdomains", "email", "rdap", "cert")

# (host, öncelikli mi) → tarama sonucu (testler için değiştirilebilir)
ScanSubdomain = Callable[[str, bool], Awaitable[SubdomainScan | None]]
CertProbe = Callable[[str], Awaitable[CertInfo | None]]


class ExternalUnavailableError(Exception):
    """Hiçbir parça tamamlanamadı (tüm servisler/DNS erişilemez)."""


@dataclass
class _Env:
    settings: Settings
    guard: UrlGuard
    client: ServiceClient
    dns: DnsClient
    visit: SafeFetcher  # alt alan adı ana sayfası (parmak izi) ve arşiv yolu denemeleri


def _matches(check_id: str, patterns: list[str]) -> bool:
    return any(check_id.startswith(p[:-1]) if p.endswith("*") else check_id == p for p in patterns)


def subdomain_checks(settings: Settings) -> list[Check]:
    return [
        c
        for c in load_builtin_checks().for_target(TargetType.WEB, 1)
        if c.phase == 1 and _matches(c.id, settings.deep_subdomain_check_ids)
    ]


def _all_public(ips: list[str]) -> bool:
    parsed = [parse_ip(i) for i in ips]
    return bool(parsed) and all(p is not None and is_public_ip(p) for p in parsed)


class ExternalModule(DeepModule):
    name = "external"
    lane = Lane.NETWORK

    def __init__(
        self,
        settings: Settings | None = None,
        guard: UrlGuard | None = None,
        dns: DnsClient | None = None,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
        limiter: ServiceLimiter | None = None,
        scan_subdomain: ScanSubdomain | None = None,
        cert_probe: CertProbe | None = None,
    ) -> None:
        self._settings = settings
        self._guard = guard
        self._dns = dns
        self._transport_factory = transport_factory
        self._limiter = limiter
        self._scan_subdomain = scan_subdomain
        self._cert_probe = cert_probe

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def budget_s(self, settings: Settings) -> float:
        return settings.deep_budget_external_s

    async def run(self, ctx: DeepContext, out: ModuleOutput) -> None:
        s = self.settings
        host = (urlsplit(ctx.target_url).hostname or "").lower()
        data = ExternalData(
            host=host,
            domain=registered_domain(host),
            shared_host=is_shared_hosting(host, s.deep_shared_host_suffixes),
        )
        ctx.external = data
        deadline = time.monotonic() + self.budget_s(s) - CHECK_BUDGET_S
        async with self._env() as env:
            parts: dict[str, Awaitable[str]] = {
                "wayback": self._wayback(env, ctx, data),
            }
            if data.shared_host:
                for part in SHARED_HOST_PARTS:
                    data.status[part] = PartStatus.SHARED_HOST
            else:
                parts["subdomains"] = self._subdomains(env, data, deadline)
                parts["email"] = self._email(env, data)
                parts["rdap"] = self._rdap(env, data)
                parts["cert"] = self._cert(env, ctx, data)
            await self._run_parts(data, parts, deadline)
        await self._emit(ctx, data)
        if not any(data.ok(p) for p in data.status):
            raise ExternalUnavailableError()
        ctx.meta["external"] = summary(data)
        skipped = data.skipped_checks()
        if skipped:
            ctx.note_checks("checks_skipped", skipped)
        checks = [
            c
            for c in load_builtin_checks().for_target(TargetType.WEB, 2)
            if c.phase == 2 and c.module == self.name and c.id not in skipped
        ]
        result = await run_checks(ctx, checks)
        out.findings.extend(result.findings)
        if result.failed:
            ctx.note_checks("checks_failed", result.failed)

    # --- parçalar

    async def _run_parts(
        self, data: ExternalData, parts: dict[str, Awaitable[str]], deadline: float
    ) -> None:
        async def guarded(name: str, coro: Awaitable[str]) -> None:
            try:
                data.status[name] = await coro
            except ServiceError as exc:
                data.status[name] = exc.status
            except (DnsError, TargetValidationError, FetchError) as exc:
                logger.info("dış görünürlük parçası alınamadı: %s (%s)", name, type(exc).__name__)
                data.status[name] = PartStatus.UNAVAILABLE
            except Exception as exc:
                logger.warning(
                    "dış görünürlük parçası başarısız: %s (%s)", name, type(exc).__name__
                )
                data.status[name] = PartStatus.UNAVAILABLE

        tasks = [asyncio.create_task(guarded(n, c)) for n, c in parts.items()]
        _, pending = await asyncio.wait(tasks, timeout=max(0.0, deadline - time.monotonic()))
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for name in parts:
            if data.status[name] == PartStatus.PENDING:
                data.status[name] = PartStatus.TIMEOUT

    async def _subdomains(self, env: _Env, data: ExternalData, deadline: float) -> str:
        s = env.settings
        names = await self._ct_names(env, data)
        skip = twins(data.host)
        data.subdomains_found = len([n for n in names if n not in skip])
        candidates = select_candidates(
            names,
            data.host,
            data.domain,
            s.deep_subdomain_priority_words,
            s.deep_subdomain_max_candidates,
        )
        sem = asyncio.Semaphore(s.deep_subdomain_dns_concurrency)

        async def inspect(host: str) -> tuple[list[str], list[str] | None]:
            async with sem:
                return await cname_chain(env.dns, host), await addresses(env.dns, host)

        results = await asyncio.gather(*(inspect(h) for h, _ in candidates), return_exceptions=True)
        if candidates and all(isinstance(r, BaseException) for r in results):
            raise DnsError("all_failed")
        alive: list[tuple[str, bool]] = []
        risky: list[tuple[str, list[str]]] = []
        for (host, priority), result in zip(candidates, results, strict=True):
            if isinstance(result, BaseException):
                continue
            chain, ips = result
            if ips and _all_public(ips):
                alive.append((host, priority))
            if chain:
                risky.append((host, chain))
        data.subdomains_alive = len(alive)
        await self._takeovers(env, data, risky)
        await self._scan_alive(env, data, alive[: s.deep_max_subdomains], deadline)
        return PartStatus.OK

    async def _ct_names(self, env: _Env, data: ExternalData) -> list[str]:
        """Sertifika kayıtlarındaki adlar: crt.sh, olmazsa Cert Spotter. İkisi de olmazsa
        crt.sh'ın hatası yukarı çıkar (parça durumu)."""
        s = env.settings
        try:
            payload = await env.client.get_json(
                "crtsh", crtsh_url(s.deep_crtsh_endpoint, data.domain)
            )
            data.subdomain_source = "crtsh"
            return parse_names(payload or [], data.domain)
        except ServiceError as first:
            try:
                payload = await env.client.get_json(
                    "certspotter", certspotter_url(s.deep_certspotter_endpoint, data.domain)
                )
            except ServiceError:
                raise first from None
            data.subdomain_source = "certspotter"
            return parse_certspotter(payload or [], data.domain)

    async def _takeovers(
        self, env: _Env, data: ExternalData, risky: list[tuple[str, list[str]]]
    ) -> None:
        sem = asyncio.Semaphore(env.settings.deep_subdomain_dns_concurrency)

        async def one(host: str, chain: list[str]) -> None:
            async with sem:
                try:
                    risk = await assess(host, chain, env.dns, lambda h: self._home_text(env, h))
                except DnsError:
                    return
            if risk is not None:
                data.takeovers.append(risk)

        await asyncio.gather(*(one(h, c) for h, c in risky))
        data.takeovers.sort(key=lambda r: r.host)

    async def _home_text(self, env: _Env, host: str) -> str | None:
        for url in (f"https://{host}/", f"http://{host}/"):
            try:
                res = await env.visit.fetch(url, HOME_BODY_BYTES)
            except FetchError:
                continue
            return res.text
        return None

    async def _scan_alive(
        self, env: _Env, data: ExternalData, alive: list[tuple[str, bool]], deadline: float
    ) -> None:
        if not alive:
            return
        s = env.settings
        sem = asyncio.Semaphore(s.deep_subdomain_scan_concurrency)
        scan = self._scan_subdomain or (lambda h, p: self._scan_one(env, h, p))

        async def one(host: str, priority: bool) -> SubdomainScan | None:
            async with sem:
                try:
                    async with asyncio.timeout(s.deep_subdomain_scan_timeout_s):
                        return await scan(host, priority)
                except TimeoutError:
                    return None

        tasks = [asyncio.create_task(one(h, p)) for h, p in alive]
        timeout = max(0.0, deadline - time.monotonic() - SCAN_MARGIN_S)
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in tasks:  # öncelik sırası korunur
            if task.cancelled() or task.exception() is not None:
                continue
            result = task.result()
            if result is not None:
                data.subdomains.append(result)

    async def _scan_one(self, env: _Env, host: str, priority: bool) -> SubdomainScan | None:
        """Alt alan adında seçili Faz 1 check'leri (aynı SSRF ve limit kuralları, daha hafif)."""
        s = env.settings
        light = s.model_copy(
            update={
                "web_max_internal_pages": 0,
                "web_cookieless_extra_samples": 0,
                "max_js_files": s.deep_subdomain_max_js_files,
                "max_total_bytes": s.deep_subdomain_max_total_bytes,
            }
        )
        ctx = await self._collect_subdomain(env, light, host)
        if ctx is None:
            return None
        try:
            result = await run_checks(ctx, subdomain_checks(s))
        finally:
            await ctx.aclose()
        return SubdomainScan(
            host=host,
            url=ctx.final_url,
            priority=priority,
            findings=[SubFinding(f.check_id, str(f.severity), f.evidence) for f in result.findings],
        )

    async def _collect_subdomain(self, env: _Env, light: Settings, host: str) -> WebContext | None:
        collector = WebCollector(light, env.guard, self._transport_factory)
        for url in (f"https://{host}/", f"http://{host}/"):
            try:
                ctx = await collector.collect(uuid.uuid4(), {"url": url})
            except CollectorError as exc:
                if exc.code == "site_blocked_bot":
                    return None
                if exc.code == "site_http_error":
                    # Ana sayfası 4xx/5xx dönen API/eski alt alan adlarında da dosyalar açık
                    # olabilir: dosya ve HTTPS check'leri için yalın bağlam.
                    return self._bare_context(env, light, url)
                continue
            return ctx if isinstance(ctx, WebContext) else None
        return None

    def _bare_context(self, env: _Env, light: Settings, url: str) -> WebContext:
        transport = self._transport_factory() if self._transport_factory else None
        return WebContext(
            target_type=TargetType.WEB,
            target_label=url.rstrip("/"),
            requested_url=url,
            final_url=url,
            fetcher=SafeFetcher(light, env.guard, transport),
            probe_max_bytes=light.probe_max_bytes,
        )

    async def _email(self, env: _Env, data: ExternalData) -> str:
        data.email = await collect_email(env.dns, data.domain, env.settings.deep_spf_max_lookups)
        return PartStatus.OK

    async def _wayback(self, env: _Env, ctx: DeepContext, data: ExternalData) -> str:
        s = env.settings
        payload = await env.client.get_json(
            "wayback", cdx_url(s.deep_wayback_endpoint, data.host, s.deep_wayback_max_rows)
        )
        rows = parse_rows(payload or [])
        data.archived_rows = len(rows)
        items = sensitive_paths(rows, data.host, s.deep_wayback_patterns)
        if not items:
            return PartStatus.OK
        parts = urlsplit(ctx.target_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        prober = WebContext(
            target_type=TargetType.WEB,
            target_label=origin,
            final_url=origin + "/",
            fetcher=SafeFetcher(s, env.guard, self._new_transport()),
            probe_max_bytes=s.probe_max_bytes,
        )
        try:
            data.archived = await probe_paths(prober, items, s.deep_wayback_max_probes)
        finally:
            await prober.aclose()
        return PartStatus.OK

    async def _rdap(self, env: _Env, data: ExternalData) -> str:
        payload = await env.client.get_json(
            "rdap", rdap_url(env.settings.deep_rdap_endpoint, data.domain)
        )
        expires = parse_expiry(payload)
        if expires is None:
            return PartStatus.NO_DATA
        data.domain_expiry = DomainExpiry(data.domain, expires)
        return PartStatus.OK

    async def _cert(self, env: _Env, ctx: DeepContext, data: ExternalData) -> str:
        if not ctx.target_url.startswith("https://"):
            return PartStatus.NO_DATA
        if self._cert_probe is not None:
            cert = await self._cert_probe(data.host)
        else:
            cert = await fetch_cert(data.host, env.guard, env.settings.deep_cert_timeout_s)
        if cert is None:
            return PartStatus.NO_DATA
        data.cert = cert
        return PartStatus.OK

    # --- altyapı

    def _new_transport(self) -> httpx.AsyncBaseTransport | None:
        return self._transport_factory() if self._transport_factory else None

    @contextlib.asynccontextmanager
    async def _env(self) -> AsyncIterator[_Env]:
        s = self.settings
        guard = self._guard or UrlGuard(timeout_s=s.dns_timeout_s)
        # Toplam süre ServiceClient'ta servis başına sınırlanır; httpx'in okuma zaman aşımı
        # (parçalar arası) en uzun servis süresinden kısa olmasın
        longest = max(s.deep_external_timeouts_s.values(), default=s.http_request_timeout_s)
        service_settings = s.model_copy(update={"http_request_timeout_s": longest})
        redis_client: Any = None
        limiter = self._limiter
        if limiter is None:
            from redis.asyncio import Redis

            redis_client = Redis.from_url(s.redis_url)
            limiter = RedisServiceLimiter(redis_client, s.deep_external_rate_limits)
        client = ServiceClient(
            SafeFetcher(service_settings, guard, self._new_transport()),
            limiter,
            s.deep_external_max_response_bytes,
            s.deep_external_timeouts_s,
        )
        visit = SafeFetcher(s, guard, self._new_transport())
        try:
            yield _Env(s, guard, client, self._dns or SystemDns(s.deep_dns_timeout_s), visit)
        finally:
            await client.aclose()
            await visit.aclose()
            if redis_client is not None:
                await redis_client.aclose()

    async def _emit(self, ctx: DeepContext, data: ExternalData) -> None:
        if data.ok("subdomains"):
            await ctx.events.emit(
                self.name,
                "deep.event.subdomains_found",
                found=data.subdomains_found,
                scanned=len(data.subdomains),
            )
        if data.ok("wayback"):
            await ctx.events.emit(self.name, "deep.event.archive_checked", count=data.archived_rows)
        if any(
            v in (PartStatus.UNAVAILABLE, PartStatus.RATE_LIMITED, PartStatus.TIMEOUT)
            for v in data.status.values()
        ):
            await ctx.events.emit(self.name, "deep.event.external_partial")


def summary(data: ExternalData) -> dict[str, Any]:
    """Rapor için özet (`scan.meta.external`). Kişisel veri yok; sadece alan adı bilgileri."""
    email = data.email
    return {
        "domain": data.domain,
        "shared_host": data.shared_host,
        "status": dict(data.status),
        "subdomain_source": data.subdomain_source,
        "subdomains_found": data.subdomains_found,
        "subdomains_alive": data.subdomains_alive,
        "subdomains_scanned": [s.host for s in data.subdomains],
        "archived_rows": data.archived_rows,
        "email": None
        if email is None
        else {
            "has_mx": email.has_mx,
            "spf": len(email.spf_records) == 1,
            "spf_all": email.spf_all,
            "dmarc_policy": email.dmarc_policy if email.dmarc_present else None,
        },
        "domain_expires_at": data.domain_expiry.expires_at.date().isoformat()
        if data.domain_expiry
        else None,
        "cert": None
        if data.cert is None
        else {"expires_at": data.cert.not_after.date().isoformat(), "issuer": data.cert.issuer},
    }
