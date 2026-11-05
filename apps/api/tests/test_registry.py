import pytest

from launchcheck.checks.base import Category, Check, Finding, ScanContext, Severity, TargetType
from launchcheck.checks.registry import Registry, run_checks


def make_check(
    check_id: str = "web.test.ok",
    target: TargetType = TargetType.WEB,
    phase: int = 1,
) -> type[Check]:
    class _C(Check):
        id = check_id
        category = Category.SECURITY
        default_severity = Severity.HIGH

        async def run(self, ctx: ScanContext) -> list[Finding]:
            return [self.finding({"x": 1})]

    _C.target = target
    _C.phase = phase
    return _C


def ctx() -> ScanContext:
    return ScanContext(target_type=TargetType.WEB, target_label="https://example.com")


def test_register_and_filter() -> None:
    reg = Registry()
    reg.register(make_check("web.a.one"))
    reg.register(make_check("web.a.phase2", phase=2))
    reg.register(make_check("apk.a.one", target=TargetType.APK))
    assert [c.id for c in reg.for_target(TargetType.WEB, 1)] == ["web.a.one"]
    assert [c.id for c in reg.for_target(TargetType.WEB, 2)] == ["web.a.one", "web.a.phase2"]
    assert [c.id for c in reg.for_target(TargetType.APK, 1)] == ["apk.a.one"]


def test_duplicate_id_rejected() -> None:
    reg = Registry()
    reg.register(make_check("web.a.one"))
    with pytest.raises(ValueError, match="zaten"):
        reg.register(make_check("web.a.one"))


@pytest.mark.parametrize("bad_id", ["web.only", "Web.a.b", "apk.a.b", "web.a.b.c", "web.a-b.c"])
def test_invalid_id_rejected(bad_id: str) -> None:
    with pytest.raises(ValueError):
        Registry().register(make_check(bad_id))


async def test_failing_check_does_not_stop_scan() -> None:
    class Boom(Check):
        id = "web.test.boom"
        target = TargetType.WEB
        category = Category.SECURITY
        default_severity = Severity.LOW

        async def run(self, ctx: ScanContext) -> list[Finding]:
            raise RuntimeError("sk-gizli-icerik")

    reg = Registry()
    reg.register(Boom)
    reg.register(make_check("web.test.ok"))
    result = await run_checks(ctx(), reg.all())
    assert result.failed == ["web.test.boom"]
    assert [f.check_id for f in result.findings] == ["web.test.ok"]


async def test_foreign_check_id_findings_dropped() -> None:
    class Liar(Check):
        id = "web.test.liar"
        target = TargetType.WEB
        category = Category.SECURITY
        default_severity = Severity.LOW

        async def run(self, ctx: ScanContext) -> list[Finding]:
            return [Finding("web.other.id", Severity.CRITICAL, Category.SECURITY)]

    reg = Registry()
    reg.register(Liar)
    result = await run_checks(ctx(), reg.all())
    assert result.findings == []
    assert result.failed == []
