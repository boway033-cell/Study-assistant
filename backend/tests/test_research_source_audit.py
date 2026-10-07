"""Original evidence checks and audit-only recovery preserve user work."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.api import study
from backend.app.core import database
from backend.app.models import Base, Book, Chunk, KnowledgeNote, StudyReport
from backend.app.services import research_audit
from backend.app.worker import tasks


@pytest.fixture
def corpus(monkeypatch):
    engine = create_engine('sqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    cfg = {'configured': True, 'provider_id': 'test', 'model': 'test-model',
           'base_url': 'https://example.test/v1', 'api_key': 'test-key'}
    monkeypatch.setattr(database, 'SessionLocal', factory)
    monkeypatch.setattr(study, 'load_llm_config', lambda *_: cfg)
    monkeypatch.setattr(tasks, 'update_progress', lambda *_args, **_kwargs: None)
    with factory() as db:
        book = Book(title='试点资料', file_path='test.pdf', file_type='pdf', status='ready')
        db.add(book); db.flush()
        chunk = Chunk(book_id=book.id, chunk_index=0, page_start=2, page_end=2,
                      content='政策效果只在试点地区得到支持。改善比例为10%，不能外推至全国。')
        db.add(chunk); db.flush()
        ref = f'B{book.id}:P2:C{chunk.id}'
        draft = f'# 试点范围\n\n改善仅见于试点地区。[{ref}]'
        packet = research_audit.source_packet(db, draft, {ref}, [book.id])
        selection = {'audit_source_text': draft, 'research_mode': 'critical',
                     'source_audit': {'status': 'failed', 'packet': packet},
                     'citation_notes': [{'number': 1, 'anchor': ref}]}
        report = StudyReport(book_ids_json=json.dumps([book.id]), focus='试点效应',
                             content='# 已保留正文\n改善仅见于试点地区。¹',
                             selection_json=json.dumps(selection, ensure_ascii=False))
        db.add(report); db.commit()
        ids = SimpleNamespace(book=book.id, chunk=chunk.id, report=report.id, ref=ref)
    yield factory, cfg, ids
    engine.dispose()


def _answer(ref, quote='政策效果只在试点地区得到支持。'):
    return {'claims': [{'claim': '证据仅支持试点地区的改善', 'source_refs': [ref],
                        'status': 'supported', 'confidence': 'high',
                        'synthesis_relation': 'consensus',
                        'evidence_quotes': [{'source_ref': ref, 'quote': quote}],
                        'reason': '原文限定在试点地区'}],
            'open_questions': ['全国是否成立'],
            'logic_review': {'central_claim': '结论应限于试点', 'gaps': ['不能据此推断全国效果']}}


def test_quote_matching_tolerates_layout_without_changing_facts(corpus):
    factory, _, ids = corpus
    with factory() as db:
        _, _, _, packet, _ = study._saved_audit_input(db, db.get(StudyReport, ids.report))
    raw = _answer(ids.ref, '“政策效果只在\n试点地区得到支持”')['claims']
    claims = research_audit.verify_quotes(study._normalize_claims(raw, {ids.ref}), raw, packet)
    assert claims[0]['status'] == 'supported'
    assert claims[0]['evidence_quotes'][0]['matched'] is True
    assert claims[0]['synthesis_relation'] == 'single_source'
    assert claims[0]['human_review_required'] is True


@pytest.mark.parametrize('quote', ['改善比例为90%', '政策效果在全国得到支持', '不存在的原文证据'])
def test_anchor_exists_but_false_quote_is_downgraded(corpus, quote):
    factory, _, ids = corpus
    with factory() as db:
        _, _, _, packet, _ = study._saved_audit_input(db, db.get(StudyReport, ids.report))
    raw = _answer(ids.ref, quote)['claims']
    claims = research_audit.verify_quotes(study._normalize_claims(raw, {ids.ref}), raw, packet)
    assert claims[0]['status'] == 'needs_review'
    assert claims[0]['confidence'] == 'low'


def test_source_packet_rejects_cross_book_anchor_and_changed_page(corpus):
    factory, _, ids = corpus
    with factory() as db:
        for ref in (f'B999:P2:C{ids.chunk}', f'B{ids.book}:P8:C{ids.chunk}'):
            assert not research_audit.source_packet(db, f'[{ref}]', {ref}, [ids.book])['entries']


def test_reaudit_only_calls_audit_and_preserves_completed_prose(corpus, monkeypatch):
    factory, cfg, ids = corpus
    calls = []
    class Provider:
        async def stream_chat(self, messages):
            calls.append(messages)
            assert '原始材料' in messages[-1]['content']
            assert '改善比例为10%' in messages[-1]['content']
            yield json.dumps(_answer(ids.ref), ensure_ascii=False)
    monkeypatch.setattr(study.LLMRouter, 'get', lambda *_: Provider())
    result = asyncio.run(study.run_report_audit(SimpleNamespace(id='audit-1'), ids.report,
                                                study._route_signature(cfg)))
    with factory() as db:
        report = db.get(StudyReport, ids.report)
        selection = json.loads(report.selection_json)
        assert report.content == '# 已保留正文\n改善仅见于试点地区。¹'
        assert json.loads(report.claims_json)[0]['evidence_quotes'][0]['matched']
        assert selection['source_audit']['status'] == 'complete'
        assert selection['source_audit']['logic_review']['gaps']
        payload = study._report_payload(report)
        assert 'packet' not in payload['source_audit']
        assert 'audit_source_text' not in payload['selection']
    assert result['report_id'] == ids.report
    assert len(calls) == 1


def test_reaudit_retains_human_verdict_and_stores_ai_suggestions(corpus, monkeypatch):
    factory, cfg, ids = corpus
    manual = [{'claim': '人工修订判断', 'status': 'partial', 'source_refs': [ids.ref],
               'human_review_required': False}]
    with factory() as db:
        report = db.get(StudyReport, ids.report)
        selection = json.loads(report.selection_json)
        selection['claims_reviewed_at'] = '2026-10-07T12:00:00Z'
        report.selection_json = json.dumps(selection)
        report.claims_json = json.dumps(manual)
        db.commit()
    class Provider:
        async def stream_chat(self, messages):
            yield json.dumps(_answer(ids.ref))
    monkeypatch.setattr(study.LLMRouter, 'get', lambda *_: Provider())
    asyncio.run(study.run_report_audit(SimpleNamespace(id='audit-manual'), ids.report,
                                      study._route_signature(cfg)))
    with factory() as db:
        report = db.get(StudyReport, ids.report)
        assert json.loads(report.claims_json) == manual
        assert json.loads(report.selection_json)['source_audit']['suggested_claims']


def test_changed_source_is_rejected_before_model_call(corpus, monkeypatch):
    factory, cfg, ids = corpus
    calls = []
    with factory() as db:
        db.get(Chunk, ids.chunk).content = '重新解析后的不同事实'
        db.commit()
    monkeypatch.setattr(study.LLMRouter, 'get', lambda *_: calls.append(True))
    with pytest.raises(ValueError, match='来源已修改'):
        asyncio.run(study.run_report_audit(SimpleNamespace(id='audit-changed'), ids.report,
                                          study._route_signature(cfg)))
    with factory() as db:
        with pytest.raises(HTTPException) as error:
            study.estimate_report_audit(ids.report, db)
        assert error.value.status_code == 409
    assert not calls


def test_edit_during_audit_is_not_overwritten(corpus, monkeypatch):
    factory, cfg, ids = corpus
    class Provider:
        async def stream_chat(self, messages):
            with factory() as db:
                report = db.get(StudyReport, ids.report)
                report.claims_json = json.dumps([{'claim': '刚保存的人工修改'}])
                db.commit()
            yield json.dumps(_answer(ids.ref))
    monkeypatch.setattr(study.LLMRouter, 'get', lambda *_: Provider())
    with pytest.raises(ValueError, match='已保留你的修改'):
        asyncio.run(study.run_report_audit(SimpleNamespace(id='audit-edit'), ids.report,
                                          study._route_signature(cfg)))
    with factory() as db:
        report = db.get(StudyReport, ids.report)
        assert json.loads(report.claims_json)[0]['claim'] == '刚保存的人工修改'
        assert json.loads(report.selection_json)['source_audit']['status'] == 'failed'


def test_invalid_audit_can_be_retried_without_rewriting_prose(corpus, monkeypatch):
    factory, cfg, ids = corpus
    responses = ['{"broken":true}', json.dumps(_answer(ids.ref))]
    class Provider:
        async def stream_chat(self, messages):
            yield responses.pop(0)
    monkeypatch.setattr(study.LLMRouter, 'get', lambda *_: Provider())
    with pytest.raises(ValueError, match='结构不完整'):
        asyncio.run(study.run_report_audit(SimpleNamespace(id='audit-fail'), ids.report,
                                          study._route_signature(cfg)))
    asyncio.run(study.run_report_audit(SimpleNamespace(id='audit-retry'), ids.report,
                                      study._route_signature(cfg)))
    with factory() as db:
        report = db.get(StudyReport, ids.report)
        assert json.loads(report.selection_json)['source_audit']['status'] == 'complete'
        assert report.content == '# 已保留正文\n改善仅见于试点地区。¹'


def test_submit_uses_confirmed_budget_and_atomic_reservation(corpus, monkeypatch):
    factory, _, ids = corpus
    captured = {}
    def reserve(name, factory, **kwargs):
        captured.update(name=name, **kwargs)
        return SimpleNamespace(id='queued-audit')
    monkeypatch.setattr(tasks, 'submit_unique', reserve)
    with factory() as db:
        estimate = study.estimate_report_audit(ids.report, db)
        req = study.StudyAuditReq(budget_max_tokens=estimate['estimated_tokens'] * 2,
                                 budget_max_calls=3, route_signature=estimate['route_signature'])
        assert study.submit_report_audit(ids.report, req, db) == {'task_id': 'queued-audit'}
    assert captured['budget_max_tokens'] == req.budget_max_tokens
    assert captured['budget_max_calls'] == 3
    assert captured['initial_result']['report_id'] == ids.report


@pytest.mark.parametrize('audit_result', ['invalid', 'cancel', 'shutdown', 'complete'])
def test_initial_report_is_committed_before_audit_and_survives_failure(corpus, monkeypatch, audit_result):
    factory, _, ids = corpus
    record = SimpleNamespace(id='new-report-task', result=None, progress=0)
    calls = []
    monkeypatch.setattr(study, '_book_context', lambda *_: '材料预览')
    monkeypatch.setattr(study, '_prepare_research_evidence',
                        lambda *_args, **_kwargs: (f'[{ids.ref}]试点原文', {ids.ref}, []))
    class Provider:
        async def stream_chat(self, messages):
            calls.append(messages)
            if len(calls) == 1:
                yield json.dumps({'report_markdown': f'# 新报告\n试点改善。[{ids.ref}]',
                                  'claims': _answer(ids.ref)['claims']})
                return
            # A separate session must see the completed draft before the model
            # can time out, return malformed output or be cancelled.
            with factory() as db:
                report = db.get(StudyReport, record.result['report_id'])
                assert report.content.startswith('# 新报告')
                assert json.loads(report.selection_json)['source_audit']['status'] == 'running'
                assert json.loads(report.claims_json)[0]['status'] == 'needs_review'
            if audit_result == 'cancel':
                raise tasks.TaskCancelled('用户停止核查')
            if audit_result == 'shutdown':
                raise asyncio.CancelledError()
            answer = _answer(ids.ref)
            answer['open_questions'] = None
            yield '{"broken":true}' if audit_result == 'invalid' else json.dumps(answer)
    monkeypatch.setattr(study.LLMRouter, 'get', lambda *_: Provider())
    work = study.run_overview(record, [ids.book], focus='试点效应', reasoning_depth='standard')
    if audit_result in {'cancel', 'shutdown'}:
        with pytest.raises((tasks.TaskCancelled, asyncio.CancelledError)):
            asyncio.run(work)
    else:
        result = asyncio.run(work)
        assert result['report_id'] == record.result['report_id']
    with factory() as db:
        report = db.get(StudyReport, record.result['report_id'])
        selection = json.loads(report.selection_json)
        assert report.content.startswith('# 新报告')
        assert selection['source_audit']['status'] == ('complete' if audit_result == 'complete' else 'failed')
        if audit_result != 'complete':
            assert json.loads(report.claims_json)[0]['status'] == 'needs_review'
    assert len(calls) == 2


def test_estimate_prevents_duplicate_audit_of_running_report(corpus, monkeypatch):
    factory, _, ids = corpus
    monkeypatch.setattr(tasks, 'get_task', lambda *_: SimpleNamespace(status='running'))
    with factory() as db:
        report = db.get(StudyReport, ids.report)
        selection = json.loads(report.selection_json)
        selection['source_audit'].update(status='running', task_id='initial-overview')
        report.selection_json = json.dumps(selection)
        db.commit()
        with pytest.raises(HTTPException) as error:
            study.estimate_report_audit(ids.report, db)
        assert error.value.status_code == 409
        assert '正在核查' in error.value.detail


def test_legacy_source_version_warning_survives_later_audits(corpus):
    factory, _, ids = corpus
    with factory() as db:
        report = db.get(StudyReport, ids.report)
        selection = json.loads(report.selection_json)
        selection['source_audit']['legacy_source_version'] = True
        report.selection_json = json.dumps(selection)
        db.commit()
        estimate = study.estimate_report_audit(ids.report, db)
        assert '无法确认生成时的来源版本' in estimate['boundary']


def test_user_notes_are_not_presented_as_book_originals(corpus):
    factory, _, ids = corpus
    with factory() as db:
        note = KnowledgeNote(book_id=ids.book, title='我的解释', content='这是用户对试点材料的个人理解。')
        db.add(note); db.flush()
        ref = f'B{ids.book}:NOTE{note.id}'
        packet = research_audit.source_packet(db, f'[{ref}]', {ref}, [ids.book])
        assert packet['entries'][0]['kind'] == 'note'
        messages = research_audit.audit_messages('用户笔记判断', packet, 'critical')
        assert '用户笔记《我的解释》（非书籍原文）' in messages[-1]['content']
        raw = _answer(ref, '这是用户对试点材料的个人理解。')['claims']
        claims = research_audit.verify_quotes(study._normalize_claims(raw, {ref}), raw, packet)
        assert claims[0]['evidence_quotes'][0]['source_kind'] == 'note'
        assert claims[0]['evidence_quotes'][0]['matched'] is True


def test_audit_bounds_original_material_and_prioritizes_actual_citations(corpus):
    factory, _, ids = corpus
    with factory() as db:
        chunks = [Chunk(book_id=ids.book, chunk_index=i + 1, page_start=i + 3, page_end=i + 3,
                        content=f'开头{i}。' + '原始证据文本。' * 600 + f'结尾{i}。') for i in range(28)]
        db.add_all(chunks); db.flush()
        refs = {f'B{ids.book}:P{row.page_start}:C{row.id}' for row in chunks} | {ids.ref}
        cited = f'B{ids.book}:P{chunks[-1].page_start}:C{chunks[-1].id}'
        packet = research_audit.source_packet(db, f'正文引用[{cited}]', refs, [ids.book])
        assert len(packet['entries']) == research_audit.MAX_SOURCES
        assert packet['entries'][0]['source_ref'] == cited
        assert packet['included_cited_refs'] == 1
        assert packet['requested_refs'] == 29
        assert sum(len(entry['text']) for entry in packet['entries']) <= research_audit.MAX_EVIDENCE_CHARS
        assert '【中间原文省略】' in packet['entries'][0]['text']


def test_public_api_exposes_audit_routes_and_enforces_confirmed_budget(corpus, monkeypatch):
    from backend.app.main import app
    factory, _, ids = corpus
    def dependency():
        with factory() as db:
            yield db
    monkeypatch.setitem(app.dependency_overrides, database.get_db, dependency)
    monkeypatch.setattr(tasks, 'submit_unique', lambda *_args, **_kwargs: SimpleNamespace(id='api-audit'))
    client = TestClient(app, base_url='http://127.0.0.1:8000',
                        headers={'Origin': 'http://127.0.0.1:8000'})
    assert client.get('/api/health').json()['capabilities']['research_source_audit'] is True
    response = client.post(f'/api/study/reports/{ids.report}/audit/estimate')
    assert response.status_code == 200
    estimate = response.json()
    request = {'route_signature': estimate['route_signature'], 'budget_max_calls': 3,
               'budget_max_tokens': estimate['estimated_tokens'] - 1}
    assert client.post(f'/api/study/reports/{ids.report}/audit', json=request).status_code == 409
    request['budget_max_tokens'] = estimate['estimated_tokens'] * 2
    response = client.post(f'/api/study/reports/{ids.report}/audit', json=request)
    assert response.status_code == 202
    assert response.json()['task_id'] == 'api-audit'


def test_human_draft_keeps_original_sources_after_audit_replaces_claims(corpus):
    factory, _, ids = corpus
    with factory() as db:
        report = db.get(StudyReport, ids.report)
        report.claims_json = json.dumps([{'claim': '新的自动判断', 'source_refs': []}])
        db.commit()
        result = study.update_report_claims(ids.report, study.StudyClaimsUpdateReq(claims=[{
            'claim': '用户正在编辑的旧判断', 'source_refs': [ids.ref, 'B999:P1:C999'],
            'status': 'partial', 'human_review_required': False,
            'verification': {'all_refs_matched': True},
        }]), db)
        assert result['claims'][0]['source_refs'] == [ids.ref]
        assert result['claims'][0]['status'] == 'partial'
        assert 'verification' not in result['claims'][0]
