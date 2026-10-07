"""A queued research task must not switch model routes after submission."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.app.api import study
from backend.app.worker import tasks


def _config(*, model: str = "model-a", key: str = "secret-a", fallback: str = "") -> dict:
    cfg = {"provider_id": "research-provider", "protocol": "openai_chat",
           "base_url": "https://provider.example/v1", "model": model,
           "api_key": key, "configured": True, "fallbacks": []}
    if fallback:
        cfg["fallbacks"] = [{"provider_id": "backup", "protocol": "openai_chat",
                             "base_url": "https://backup.example/v1", "model": fallback,
                             "api_key": "backup-secret"}]
    return cfg


def test_route_signature_tracks_model_key_and_fallback_without_exposing_key():
    original = study._route_signature(_config())
    assert original != study._route_signature(_config(model="model-b"))
    assert original != study._route_signature(_config(key="secret-b"))
    assert original != study._route_signature(_config(fallback="backup-model"))
    assert "secret-a" not in original


def test_execution_rejects_changed_route_before_model_call(monkeypatch):
    expected = study._route_signature(_config())
    monkeypatch.setattr(study, "load_llm_config", lambda _db, _task: _config(model="model-b"))

    with pytest.raises(ValueError, match="重新预估并提交"):
        study._checked_research_config(None, expected)


def test_submission_rejects_a_route_changed_after_user_confirmed_estimate(monkeypatch):
    monkeypatch.setattr(study, "load_llm_config", lambda _db, _task: _config(model="model-b"))
    monkeypatch.setattr(study, "_overview_budget", lambda _req, _db, *, cfg:
                        {"configured": True, "estimated_tokens": 100, "estimated_calls": 1})
    req = study.StudyOverviewReq(book_ids=[1], focus="研究问题",
                                 budget_max_tokens=1000, budget_max_calls=2,
                                 route_signature=study._route_signature(_config(model="model-a")))
    with pytest.raises(HTTPException) as error:
        study.study_overview(req, db=None)
    assert error.value.status_code == 409


def test_estimate_returns_the_route_identity_shown_to_the_user(monkeypatch):
    cfg = _config()
    monkeypatch.setattr(study, "load_llm_config", lambda _db, _task: cfg)
    monkeypatch.setattr(study, "_overview_budget", lambda _req, _db, *, cfg:
                        {"configured": True, "model": cfg["model"]})
    estimate = study.estimate_overview(study.StudyOverviewReq(book_ids=[1], focus="研究问题"), db=None)
    assert estimate["model"] == "model-a"
    assert estimate["route_signature"] == study._route_signature(cfg)


def test_submit_captures_only_route_digest_not_api_key(monkeypatch):
    cfg = _config()
    submitted = {}
    observed = {}
    monkeypatch.setattr(study, "load_llm_config", lambda _db, _task: cfg)
    monkeypatch.setattr(study, "_overview_budget", lambda _req, _db, *, cfg:
                        {"configured": True, "estimated_tokens": 100, "estimated_calls": 1})

    async def fake_run_overview(*args):
        observed["route_signature"] = args[-1]
        return {}

    def fake_submit(_name, factory, **_kwargs):
        submitted["factory"] = factory
        return SimpleNamespace(id="queued-research")

    monkeypatch.setattr(study, "run_overview", fake_run_overview)
    monkeypatch.setattr(tasks, "submit", fake_submit)
    req = study.StudyOverviewReq(book_ids=[1], focus="研究问题",
                                 budget_max_tokens=1000, budget_max_calls=2)

    result = study.study_overview(req, db=None)
    asyncio.run(submitted["factory"](None))

    assert result == {"task_id": "queued-research"}
    assert observed["route_signature"] == study._route_signature(cfg)
    captured = [cell.cell_contents for cell in submitted["factory"].__closure__]
    assert cfg not in captured
    assert all("secret-a" not in repr(value) for value in captured)
