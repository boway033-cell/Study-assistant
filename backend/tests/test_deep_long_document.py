"""Synthetic-only regressions for the Reader deep-analysis entry point."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core.database import Base
from backend.app.models import Book, BookDeep, Chapter, Chunk
from backend.app.services.deep_analysis import build_chapter_inputs, summarize_by_toc


def test_root_ordinals_include_descendants_and_distinguish_repeated_titles():
    chapters = [SimpleNamespace(id=10, order_index=0, level=1, parent_id=None, title='同名', start_page=1),
                SimpleNamespace(id=11, order_index=1, level=2, parent_id=10, title='子节', start_page=2),
                SimpleNamespace(id=12, order_index=7, level=1, parent_id=None, title='同名', start_page=5)]
    chunks = [SimpleNamespace(id=i, book_id=1, chapter_id=cid, content=text, page_start=p, page_end=p)
              for i, cid, text, p in [(1, 10, 'ROOT', 1), (2, 11, 'CHILD', 2), (3, 12, 'TAIL', 5)]]
    toc, texts, sections = build_chapter_inputs(chapters, chunks)
    assert [t['chapter_id'] for t in toc] == [10, 11, 12]
    assert 'ROOT' in texts[1] and 'CHILD' in texts[1] and 'TAIL' not in texts[1]
    assert 'TAIL' in texts[2] and 'CHILD' not in texts[2]
    assert sections['11'] == 'CHILD'


def test_chapter_summary_reads_tail_and_emits_completed_chapter(monkeypatch):
    from backend.app.services import deep_analysis
    seen, saved = [], []
    async def stream(provider, messages, error, on_progress=None):
        seen.append(messages[-1]['content'])
        return '完整精读笔记'
    monkeypatch.setattr(deep_analysis, '_bounded_stream', stream)
    source = '甲' * 31000 + 'ONLY_AT_TAIL'
    result = asyncio.run(summarize_by_toc(None, '合成资料',
        [{'title': '第一章', 'page': 1, 'level': 1, 'chapter_id': 10}], {1: source},
        on_chapter=lambda i, item: saved.append(item)))
    assert any('ONLY_AT_TAIL' in text for text in seen)
    assert result[0]['processed_chars'] == len(source)
    assert result[0]['batches'] == 3
    assert saved == result


@pytest.mark.parametrize('failure', ['second_chapter', 'card'])
def test_deep_entry_persists_completed_chapters_on_failure(monkeypatch, failure):
    from backend.app.api import deep
    from backend.app.core import database
    from backend.app.services import llm
    from backend.app.worker import tasks
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        book = Book(title='Synthetic deep fixture', file_type='pdf', file_path='fixture.pdf', status='ready')
        db.add(book); db.flush()
        book_id = book.id
        for i in range(2):
            chapter = Chapter(book_id=book_id, title=f'Chapter {i}', order_index=i * 4, level=1, start_page=i + 1)
            db.add(chapter); db.flush()
            db.add(Chunk(book_id=book_id, chapter_id=chapter.id, chunk_index=i,
                         page_start=i + 1, page_end=i + 1, content=f'MATERIAL_{i}'))
        db.commit()
    async def summarize(provider, title, toc, texts, on_progress=None, on_chapter=None):
        results = []
        assert 'MATERIAL_0' in texts[1] and 'MATERIAL_1' in texts[2]
        for i, item in enumerate(toc, 1):
            if i == 2 and failure == 'second_chapter':
                raise RuntimeError('synthetic disconnect')
            result = {'title': item['title'], 'key': str(item['chapter_id']), 'summary': f'SAVED_{i}'}
            on_chapter(i, result)
            # Use another session to prove persistence before the next chapter.
            with factory() as verify:
                assert f'SAVED_{i}' in verify.scalar(select(BookDeep)).markdown
            results.append(result)
        return results
    async def card(*args, **kwargs):
        assert len(kwargs['summaries']) == 2
        raise RuntimeError('synthetic card failure')
    monkeypatch.setattr(database, 'SessionLocal', factory)
    monkeypatch.setattr(llm, 'load_llm_config', lambda *a: {'configured': True, 'api_key': ''})
    monkeypatch.setattr(llm.LLMRouter, 'get', lambda *a: object())
    monkeypatch.setattr(tasks, 'update_progress', lambda *a, **kw: None)
    monkeypatch.setattr(deep, 'summarize_by_toc', summarize)
    monkeypatch.setattr(deep, 'build_paper_card', card)
    record = SimpleNamespace(result=None)
    if failure == 'second_chapter':
        with pytest.raises(RuntimeError, match='disconnect'):
            asyncio.run(deep.run_deep_analysis(record, book_id))
    else:
        assert asyncio.run(deep.run_deep_analysis(record, book_id))['ai'] is True
    with factory() as db:
        artifact = db.scalar(select(BookDeep))
        assert 'SAVED_1' in artifact.markdown
        assert len(json.loads(artifact.summaries_json)) == (1 if failure == 'second_chapter' else 2)
        assert artifact.status == ('failed' if failure == 'second_chapter' else 'done')
        assert artifact.error_msg
    engine.dispose()
