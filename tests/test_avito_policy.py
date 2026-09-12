from unittest.mock import AsyncMock

import pytest

from services.avito_policy import AvitoPaused, AvitoPolicy, retry_after_seconds


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.setattr("services.avito_policy.time.time", lambda: 1000)
    monkeypatch.setattr("services.avito_policy.asyncio.sleep", AsyncMock())
    return AvitoPolicy(tmp_path / "policy.sqlite3", daily_pages=2)


async def test_budget_and_pacing_survive_instances(policy):
    await policy.before_page()
    next_at = policy.snapshot()["next_page_at"]
    assert next_at >= 1060
    other = AvitoPolicy(policy.path, daily_pages=2)
    await other.before_page()
    assert other.snapshot()["next_page_at"] >= next_at + 60
    with pytest.raises(AvitoPaused, match="budget"):
        await policy.before_page()


async def test_block_stops_other_searches_and_needs_useful_probe(policy):
    policy.block("CAPTCHA")
    with pytest.raises(AvitoPaused, match="manual"):
        await AvitoPolicy(policy.path).before_page()
    policy.request_probe()
    assert policy.take_probe()
    assert not policy.take_probe()
    policy.finish_probe(False)
    with pytest.raises(AvitoPaused):
        policy.check()
    policy.request_probe()
    assert policy.take_probe()
    policy.finish_probe(True)
    policy.check()


def test_probe_cannot_clear_retry_after(policy):
    policy.block("403")
    policy.cooldown(90000, "429")
    policy.request_probe()
    assert not policy.take_probe()
    assert policy.snapshot()["cooldown_until"] == 91000


def test_retry_after_and_bounded_retries(policy):
    assert retry_after_seconds("120", 1000) == 120
    assert retry_after_seconds("Thu, 01 Jan 1970 01:00:00 GMT", 1000) == 2600
    assert retry_after_seconds("bad", 1000) == 3600
    policy.transient_failure("timeout")
    assert policy.snapshot()["cooldown_until"] == 4600
    policy.transient_failure("timeout")
    assert policy.snapshot()["cooldown_until"] == 8200
    policy.transient_failure("timeout")
    assert policy.snapshot()["blocked"]


def test_cursor_and_due_time_persist(policy):
    policy.finish_search("flats", 11, 6)
    state = AvitoPolicy(policy.path).search_state("flats")
    assert state["next_page"] == 11
    assert 22600 <= state["due_at"] <= 24400


async def test_budget_renews_after_24h(policy, monkeypatch):
    await policy.before_page()
    await policy.before_page()
    monkeypatch.setattr("services.avito_policy.time.time", lambda: 1000 + 86401)
    await policy.before_page()
    assert policy.snapshot()["pages"] == 1


async def test_runner_skips_remaining_avito_and_keeps_other_sources(tmp_path, monkeypatch):
    import json

    import app.runner as runner
    from app.config import Settings
    from collectors.avito import AvitoCollector
    from collectors.base import CollectorBlockedError

    calls = []

    class BlockedAvito(AvitoCollector):
        async def collect_search(self, search, context):
            calls.append(search.name)
            raise CollectorBlockedError("CAPTCHA")

    class Other:
        async def collect_search(self, search, context):
            calls.append(search.name)
            return []

    async def browser(settings):
        yield object()

    config = tmp_path / "searches.yaml"
    config.write_text(
        json.dumps(
            {
                "searches": [
                    {
                        "name": name,
                        "source": source,
                        "url": "https://example.test",
                        "city": "Samara",
                    }
                    for name, source in [("avito1", "avito"), ("avito2", "avito"), ("cian", "cian")]
                ]
            }
        )
    )
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text("{}")
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'main.sqlite3'}",
        searches_config_path=config,
        scoring_config_path=scoring,
        avito_policy_path=tmp_path / "policy.sqlite3",
        telegram_bot_token=None,
    )
    monkeypatch.setattr(runner, "persistent_context", browser)
    monkeypatch.setattr(runner, "COLLECTORS", {"avito": BlockedAvito(), "cian": Other()})
    await runner.init_db(settings)
    await runner.collect_once(settings)
    assert calls.count("avito1") == 1
    assert "avito2" not in calls
    assert "cian" in calls
    calls.clear()
    await runner.collect_once(settings)
    assert calls == ["cian"]
