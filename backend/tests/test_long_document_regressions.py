import asyncio
import json
import re
from types import SimpleNamespace

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.core.database import Base
from backend.app.models import Book, Chapter, Chunk
from backend.app.services.rag.toc_import import select_import_toc
from backend.app.services.rag.toc_heuristic import classify_heading, extract_toc_heuristic
from backend.app.services.long_research import iter_evidence_batches, read_long_scope
from backend.app.api.study import _source_anchor


def test_contents_and_body_chapters_are_not_confused():
    pages = ['封面', '目录\n第一章 起源 .... 1\n第二章 演变 .... 25',
             '第一章 起源\n这是正文。', '正文。', '第二章 演变\n正文。']
    # Wrong native bookmarks on the contents page cannot override body evidence.
    native = [{'title': '第一章 起源', 'level': 1, 'page': 2},
              {'title': '第二章 演变', 'level': 1, 'page': 2}]
    result = select_import_toc('pdf', native, pages)
    assert [(r['title'], r['page']) for r in result] == [('第一章 起源', 3), ('第二章 演变', 5)]
    assert classify_heading('第三章 制度 ........ 66') is None


def test_contents_continuation_and_repeated_part_titles():
    pages = ['目录\n第一章 起源 1\n第二章 演变 25',
             '第三章 制度 66\n第四章 方法 88\n第五章 变化 100\n第六章 总结 123',
             '第一章 起源\n正文。', '第二章 演变\n正文。',
             '第一章 起源\n第二部中的同名章节正文。']
    result = extract_toc_heuristic(pages)
    assert [r['page'] for r in result] == [3, 4, 5]


def test_rapid_new_numpy_result_preserves_text_boxes():
    from backend.app.services.parser.ocr import rapid_result_rows
    box = np.array([[[1, 2], [12, 2], [12, 9], [1, 9]]])
    result = rapid_result_rows(SimpleNamespace(boxes=box, txts=['扫描文字'], scores=np.array([.97])))
    assert result[0][1:] == ['扫描文字', .97]
    assert np.array_equal(result[0][0], box[0])


def test_long_reading_covers_chunk_tails_and_selected_scope():
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        book = Book(title='Synthetic', file_path='synthetic.pdf', file_type='pdf')
        db.add(book); db.flush()
        chapter = Chapter(book_id=book.id, title='选中', level=1, order_index=0, start_page=1)
        other = Chapter(book_id=book.id, title='未选', level=1, order_index=1, start_page=2)
        db.add_all([chapter, other]); db.flush()
        book_id, chapter_id = book.id, chapter.id
        source = '甲' * 31000 + 'ONLY_AT_TAIL'
        db.add_all([Chunk(book_id=book_id, chapter_id=chapter_id, content=source, chunk_index=0, page_start=1, page_end=1),
                    Chunk(book_id=book_id, chapter_id=other.id, content='EXCLUDED', chunk_index=1, page_start=2, page_end=2)])
        db.commit()
        batches = list(iter_evidence_batches(db, [book_id], [chapter_id], _source_anchor))
        assert sum(size for _, _, size in batches) == len(source)
        assert all(len(text) <= 12000 for text, _, _ in batches)
        assert 'ONLY_AT_TAIL' in batches[-1][0]
        assert 'EXCLUDED' not in ''.join(b[0] for b in batches)
        calls, progresses = [], []
        async def stream(provider, messages, error, on_progress=None):
            calls.append(messages[-1]['content'])
            if on_progress: on_progress(10)
            return '已归纳证据'
        _, anchors, coverage = asyncio.run(read_long_scope(
            db, None, [book_id], [chapter_id], '问题', _source_anchor, stream,
            lambda done, total, message: progresses.append(done)))
        assert len(calls) == len(batches)
        assert len(anchors) == 1
        assert coverage['processed_chars'] == coverage['total_chars'] == len(source)
        assert progresses[-1] == len(source)
    engine.dispose()


def test_overview_long_scope_saves_prose_coverage_and_audit(monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from backend.app.api import study
    from backend.app.core import database
    from backend.app.worker import tasks
    from backend.app.models import StudyReport
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        book = Book(title='Synthetic research', file_path='synthetic.pdf', file_type='pdf')
        db.add(book); db.flush()
        book_id = book.id
        db.add_all([Chunk(book_id=book_id, content=f'BATCH_{i}\n' + '证据文本' * 800,
                          chunk_index=i, page_start=i + 1, page_end=i + 1) for i in range(100)])
        db.commit()
    seen = set()
    anchors = []
    class Provider:
        async def stream_chat(self, messages):
            system = messages[0]['content']
            body = messages[-1]['content']
            if '阅读这一批原始材料' in system:
                seen.update(re.findall(r'BATCH_(\d+)', body))
                refs = re.findall(r'\[(B[^\]]+)\]', body)
                anchors.extend(refs)
                yield '机制与证据。' + ''.join(f'[{ref}]' for ref in refs)
            elif '只输出 JSON 对象' in system:
                yield json.dumps({'report_outline': ['机制', '比较', '结论'], 'subquestions': ['解释差异']})
            elif '核对文章中的关键主张' in system:
                yield json.dumps({'claims': [{'claim': '跨材料判断', 'source_refs': [anchors[-1]],
                                              'status': 'partial', 'synthesis_relation': 'complementary'}]})
            else:
                yield '# 连贯论证\n' + '证据支持这个判断。' * 100 + f'[{anchors[-1]}]'
    def update(record, progress, stage, message, **kwargs):
        record.progress, record.stage, record.message = progress, stage, message
    monkeypatch.setattr(database, 'SessionLocal', factory)
    monkeypatch.setattr(tasks, 'update_progress', update)
    monkeypatch.setattr(study, '_book_context', lambda *a: 'synthetic overview')
    monkeypatch.setattr(study, 'load_llm_config', lambda *a: {'configured': True})
    monkeypatch.setattr(study.LLMRouter, 'get', lambda *a: Provider())
    record = SimpleNamespace(result=None, progress=0)
    result = asyncio.run(study.run_overview(record, [book_id], focus='比较机制'))
    with factory() as db:
        report = db.get(StudyReport, result['report_id'])
        selection = json.loads(report.selection_json)
        assert seen == {str(i) for i in range(100)}
        assert selection['reading_coverage']['processed_chars'] == selection['reading_coverage']['total_chars']
        assert report.content.startswith('# 连贯论证')
        assert result['claims'] == 1
        assert record.result['draft_markdown']
    engine.dispose()


def test_interrupted_prose_keeps_partial_draft_without_replaying():
    from backend.app.api.study import _stream_answer
    import pytest
    calls, saved = [], []
    class Provider:
        async def stream_chat(self, messages):
            calls.append(1)
            yield '已经写出的文章'
            raise ConnectionError('disconnected')
    with pytest.raises(RuntimeError, match='草稿保留'):
        asyncio.run(_stream_answer(Provider(), [], '写作失败', on_text=saved.append))
    assert calls == [1]
    assert saved[-1] == '已经写出的文章'


def test_file_hash_cache_reuses_content_until_file_changes(tmp_path):
    from backend.app.services.parser.ocr import _file_hash, _file_hash_cached
    path = tmp_path / 'sample.pdf'
    path.write_bytes(b'first')
    _file_hash_cached.cache_clear()
    before = _file_hash(path)
    assert _file_hash(path) == before
    assert _file_hash_cached.cache_info().hits == 1
    path.write_bytes(b'changed source')
    assert _file_hash(path) != before


def test_legacy_full_page_ocr_is_body_and_geometry_restores_titles(tmp_path):
    from backend.app.services.parser.structured import StructuredDocument, StructuredPage, DocumentBlock, hydrate_ocr_geometry
    from backend.app.services.analyzer.layout import analyze_structured
    page = StructuredPage(1, 600, 800, [DocumentBlock(page=1, text='正文。' * 100,
                            source='ocr', bbox=(0, 0, 600, 800))])
    document = StructuredDocument(pages=[page])
    layout = analyze_structured(document)
    assert layout.pages[0][0].block_type == 'body'
    assert layout.clean_page_text(0)
    items = [{'text': '第一章 起源', 'x': .2, 'y': .2, 'w': .6, 'h': .05},
             {'text': '这是正文。' * 5, 'x': .1, 'y': .4, 'w': .8, 'h': .025},
             {'text': '这是正文。' * 5, 'x': .1, 'y': .5, 'w': .8, 'h': .025}]
    (tmp_path / 'page_0001.layout.json').write_text(json.dumps({'version': 1, 'items': items}), encoding='utf-8')
    assert hydrate_ocr_geometry(document, tmp_path) == 1
    assert len(page.blocks) == 3
    assert page.blocks[0].bbox == (120, 160, 480, 200)
    layout = analyze_structured(document)
    assert layout.pages[0][0].block_type == 'title'


def test_split_ocr_contents_numbers_are_not_body_headers():
    from backend.app.services.rag.toc_heuristic import contents_page_numbers
    assert contents_page_numbers(['目次\n第一章\n理论起源\n15\n第二章\n制度演变\n58\n第三章\n比较分析\n103']) == {1}
    assert classify_heading('（3）虽然风险的扩散和商业化并没有完全摒弃资本主义发展,仍然延续') is None
