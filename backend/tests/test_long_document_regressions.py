import asyncio
import json
import re
from types import SimpleNamespace

import numpy as np
from sqlalchemy import create_engine, select, text
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


def test_multi_page_printed_contents_are_aggregated_without_merging_body_subheads():
    from backend.app.services.rag.toc_heuristic import contents_page_numbers, extract_contents_entries

    pages = ['封面',
             '目录\n第一章 起源 .... 1\n第一节 问题提出 .... 10',
             '第二章\n制度演变的起点\n20\n第二节 资料来源\n30\n第三章\n比较分析的方法\n40',
             '作者说明\n这是正文叙述，包含很多文字，不是目录。',
             '第一章 起源\n正文讨论起源。', '正文。',
             '第一节 问题提出\n正文讨论问题。', '正文。',
             '第二章 制度演变\n段落小标题\n正文说明制度。', '正文。',
             '第二节 资料来源\n正文说明材料。', '正文。',
             '第三章 比较分析\n正文讨论比较。']
    detected = contents_page_numbers(pages)
    assert detected == {2, 3}
    printed = extract_contents_entries(pages, detected)
    assert [row['title'] for row in printed] == [
        '第一章 起源', '第一节 问题提出', '第二章 制度演变的起点',
        '第二节 资料来源', '第三章 比较分析的方法',
    ]
    # The two wrapped titles have no identical body heading in this fixture;
    # they must not be assigned the printed page or merged with nearby prose.
    result = select_import_toc('pdf', [], pages)
    assert all(row['page'] not in detected for row in result)
    assert '段落小标题' not in [row['title'] for row in result]


def test_printed_contents_maps_all_pages_to_body_anchors_and_restores_levels():
    from backend.app.services.rag.toc_heuristic import contents_page_numbers, extract_contents_entries

    pages = ['封面', '目录\n第一章 起源 1\n第一节 问题提出 10',
             '第二章 制度演变\n20\n第二节 资料来源\n30\n第三章 比较分析\n40',
             '序言文字。', '第一章 起源\n正文。', '正文。',
             '第一节 问题提出\n正文。', '正文。',
             '第二章 制度演变\n一、正文中未列入目录的段落小标题\n正文。', '正文。',
             '第二节 资料来源\n正文。', '正文。',
             '第三章 比较分析\n正文。']
    assert contents_page_numbers(pages) == {2, 3}
    assert len(extract_contents_entries(pages)) == 5
    result = select_import_toc('pdf', [], pages)
    assert [(row['title'], row['level'], row['page']) for row in result] == [
        ('第一章 起源', 1, 5), ('第一节 问题提出', 2, 7),
        ('第二章 制度演变', 1, 9), ('第二节 资料来源', 2, 11),
        ('第三章 比较分析', 1, 13),
    ]


def test_long_book_uses_physical_native_spine_instead_of_repeated_running_headers():
    pages = ['普通正文。'] * 80
    pages[1] = '目录\n第一章 理论基础 .... 1\n第二章 制度设计 .... 30'
    pages[2] = '第三章 比较研究\n58\n第四章 结论\n70\n附录\n78'
    for page in range(10, 39):
        pages[page - 1] = '第一章 理论基础\n这里是正文段落。'
    native = [
        {'title': '目录', 'level': 1, 'page': 2},
        {'title': '第一章 理论基础', 'level': 2, 'page': 10},
        {'title': '第二章 制度设计', 'level': 2, 'page': 40},
        {'title': '第三章 比较研究', 'level': 2, 'page': 60},
        {'title': '第四章 结论', 'level': 2, 'page': 72},
    ]
    result = select_import_toc('pdf', native, pages)
    assert [(row['title'], row['level'], row['page']) for row in result] == [
        ('第一章 理论基础', 1, 10), ('第二章 制度设计', 1, 40),
        ('第三章 比较研究', 1, 60), ('第四章 结论', 1, 72),
    ]


def test_native_bookmarks_shift_back_only_with_repeated_exact_title_leaf_evidence():
    pages = ['普通正文。'] * 80
    native = []
    for number, page, title in [(1, 10, '理论基础'), (2, 28, '制度设计'),
                                (3, 47, '比较研究'), (4, 68, '研究结论')]:
        pages[page - 1] = f'第{number}章\n{title}\n正文。'
        native.append({'title': f'第{number}章 {title}', 'level': 1, 'page': page + 1})
    result = select_import_toc('pdf', native, pages)
    assert [(row['title'], row['page']) for row in result] == [
        (item['title'], page) for item, page in zip(native, [10, 28, 47, 68])
    ]


def test_number_heavy_body_table_is_not_a_contents_continuation():
    from backend.app.services.rag.toc_heuristic import contents_page_numbers

    table = '财政收入排名\n' + '\n'.join(str(index % 20) for index in range(80))
    pages = ['目录\n第一章 理论 .... 1\n第二章 实证 .... 20',
             '第三章 讨论\n40\n第四章 结论\n50\n参考文献\n60',
             '第一章 理论\n正文阐述研究问题。', table, table]
    assert contents_page_numbers(pages) == {1, 2}


def test_dotted_toc_pages_continue_without_undotted_heading_and_later_tables_stay_body():
    from backend.app.services.rag.toc_heuristic import contents_page_numbers

    pages = [
        '目录\n第一章 导论 ........ 1\n第一节 对象 ........ 2',
        '第二章 方法 ........ 20\n第二节 样本 ........ 21\n第三章 结论 ........ 40\n第四章 展望 ........ 50',
        '正文内容。',
        '\n'.join(['排名 6 1 5 10 3 12 4 9 14 7 11 13 8 2'] * 15
                  + ['2.9 13.5 22.7 27.1'] * 5 + [str(x) for x in range(25)]),
    ]
    assert contents_page_numbers(pages) == {1, 2}


def test_two_column_printed_toc_is_ordered_by_printed_pages():
    from backend.app.services.rag.toc_heuristic import extract_contents_entries

    pages = ['目录\n第 1 1 章 预算 .... 30\n11.1 支出 .... 31\n'
             '第10章 人事 .... 20\n10.1 招录 .... 21\n第12章 绩效 .... 40',
             '正文。', '第10章 人事\n正文。', '10.1 招录\n正文。', '正文。',
             '第11章 预算\n正文。', '11.1 支出\n正文。', '正文。',
             '第12章 绩效\n正文。']
    assert [entry['title'] for entry in extract_contents_entries(pages)] == [
        '第10章 人事', '10.1 招录', '第11章 预算', '11.1 支出', '第12章 绩效',
    ]
    assert [(row['title'], row['level'], row['page']) for row in select_import_toc('pdf', [], pages)] == [
        ('第10章 人事', 1, 3), ('10.1 招录', 2, 4),
        ('第11章 预算', 1, 6), ('11.1 支出', 2, 7), ('第12章 绩效', 1, 9),
    ]


def test_legacy_pdf_can_rebuild_toc_without_structured_cache(tmp_path, monkeypatch):
    import fitz
    from backend.app.core.config import settings
    from backend.app.services.rag import fts
    from backend.app.services.rag.toc_rebuild import rebuild_book_toc

    pdf = fitz.open()
    for heading in ('Introduction', 'Methods', 'Results'):
        pdf.new_page().insert_text((72, 72), heading)
    pdf.save(tmp_path / 'legacy.pdf')
    pdf.close()
    monkeypatch.setattr(settings, 'uploads_dir', tmp_path)
    monkeypatch.setattr(settings, 'structured_dir', tmp_path)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(fts._CREATE_SQL))
    with Session(engine) as db:
        book = Book(title='Legacy', file_path='legacy.pdf', file_type='pdf', total_pages=3)
        db.add(book)
        db.commit()
        result = rebuild_book_toc(db, book, allow_ocr=False)
        assert result['status'] == 'rebuilt'
        assert result['chapters'] == 3
        saved = db.scalars(select(Chapter).where(Chapter.book_id == book.id)
                           .order_by(Chapter.order_index)).all()
        assert [(row.title, row.start_page) for row in saved] == [
            ('Introduction', 1), ('Methods', 2), ('Results', 3),
        ]
    engine.dispose()


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
    section_focus = []
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
                assigned = re.search(r'本段只承担这些论证主题：(\[[^\]]+\])。', body)
                section_focus.append(json.loads(assigned.group(1)))
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
        assert section_focus == [['机制'], ['比较', '结论']]
        assert [item['focus'] for item in selection['research_plan']['writing_sections']] == section_focus
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


def test_certificate_failure_is_not_retried_as_a_slow_generation():
    from backend.app.api.study import _stream_answer
    import pytest

    calls = []

    class Provider:
        async def stream_chat(self, messages):
            calls.append(1)
            raise RuntimeError('SSL: CERTIFICATE_VERIFY_FAILED: EE certificate key too weak')
            yield ''

    with pytest.raises(RuntimeError, match='CERTIFICATE_VERIFY_FAILED'):
        asyncio.run(_stream_answer(Provider(), [], '生成失败'))
    assert calls == [1]


def test_first_token_watchdog_fits_inside_outer_timeout():
    """首字看门狗 × 重试次数 + 退避必须小于外层 asyncio.timeout。

    这两个数字是耦合的：只调大看门狗而不动重试次数，最后一次尝试会被外层
    超时直接砍掉，反而把已经收到的草稿一起丢掉（D10 的成因）。
    """
    import inspect
    import re

    from backend.app.api import study
    source = inspect.getsource(study._stream_answer)
    attempts = int(re.search(r"for attempt in range\((\d+)\)", source).group(1))
    outer = int(re.search(r"asyncio\.timeout\((\d+)\)", source).group(1))
    backoff = 2 * (1 + 2)  # await asyncio.sleep(2 * (attempt + 1)) 在 3 次尝试下的总和
    assert study.FIRST_TOKEN_TIMEOUT * attempts + backoff < outer, \
        "看门狗 × 重试 + 退避 已超出外层超时，最后一次尝试会被直接取消"


def test_stream_answer_tolerates_slow_first_token(monkeypatch):
    """首字慢但最终有内容时不得判超时——实测该模型首字常态 60–90 秒。"""
    import asyncio

    from backend.app.api import study
    monkeypatch.setattr(study, 'FIRST_TOKEN_TIMEOUT', 5)

    class SlowProvider:
        async def stream_chat(self, messages):
            await asyncio.sleep(1.5)
            yield '慢慢来的正文'

    assert asyncio.run(study._stream_answer(SlowProvider(), [], '写作失败')) == '慢慢来的正文'


def test_stream_answer_uses_the_current_watchdog_limit_in_its_message(monkeypatch):
    """超时文案必须跟着常量走，否则日志会误导排查方向。"""
    import asyncio

    import pytest

    from backend.app.api import study
    monkeypatch.setattr(study, 'FIRST_TOKEN_TIMEOUT', 1)

    class HungProvider:
        async def stream_chat(self, messages):
            await asyncio.sleep(30)
            yield 'x'

    with pytest.raises(RuntimeError, match='模型连续1秒无任何响应'):
        asyncio.run(study._stream_answer(HungProvider(), [], '写作失败'))


def test_stream_answer_waits_through_reasoning_only_phase(monkeypatch):
    """思考型模型先长时间只推 reasoning 增量、正文为零，期间绝不能判超时。

    实测活动模型（qwen3.8-max-0902）会先流式思考 131 秒才吐第一个正文字，
    期间 SSE 一直在推 reasoning_content。旧实现只看 answer 是否增长，
    于是在思考阶段误报「连续120秒未返回内容」——长报告反复失败的真正根因。
    """
    import asyncio
    import time

    from backend.app.api import study
    monkeypatch.setattr(study, 'FIRST_TOKEN_TIMEOUT', 1)

    class ThinkingProvider:
        def __init__(self):
            self.last_delta_at = 0.0
            self.reasoning_chars = 0

        async def stream_chat(self, messages):
            self.last_delta_at = time.monotonic()
            for _ in range(30):  # 约 3 秒纯思考：端点持续推帧，但不产出正文
                await asyncio.sleep(0.1)
                self.last_delta_at = time.monotonic()
                self.reasoning_chars += 10
            yield '思考完之后才出现的正文'

    seen = []
    prose = '思考完之后才出现的正文'
    assert asyncio.run(study._stream_answer(ThinkingProvider(), [], '写作失败',
                                            on_progress=seen.append)) == prose
    assert seen.count(0) >= 1, '思考阶段必须仍在推进进度，否则界面看起来就是卡死'
    assert seen[-1] == len(prose)
    assert all(count in (0, len(prose)) for count in seen), '进度里的字数只能是正文长度'


def test_reasoning_only_frames_are_not_treated_as_output(monkeypatch):
    """思考帧不得被当成「已产出」，否则 RoutedProvider 会拒绝降级。

    RoutedProvider 用 emitted 判断能否切换到备用模型；若把 reasoning 增量
    当作输出透出，主通道思考到一半失败就会直接抛错、堵死降级链。
    """
    import asyncio

    from backend.app.services.llm import LLMProvider, RoutedProvider

    class ThinkingThenDead(LLMProvider):
        def __init__(self):
            self.last_delta_at = 0.0
            self.reasoning_chars = 0

        async def stream_chat(self, messages):
            import time
            self.last_delta_at = time.monotonic()
            self.reasoning_chars += 500  # 思考过，但一帧正文都没产出
            if False:
                yield  # 保持异步生成器语义：思考帧不外泄，连接随后断开
            raise RuntimeError('thinking-only channel went down')

    monkeypatch.setattr("backend.app.services.llm._record_usage", lambda *args, **kwargs: None)

    class Backup(LLMProvider):
        name = 'backup'

        async def stream_chat(self, messages):
            yield '备用通道的正文'

    routed = RoutedProvider([ThinkingThenDead(), Backup()], 'research')

    async def collect():
        return [value async for value in routed.stream_chat([{'role': 'user', 'content': 'q'}])]

    assert asyncio.run(collect()) == ['备用通道的正文']


def test_routed_provider_exposes_heartbeat_of_active_channel(monkeypatch):
    """看门狗要能透过路由层看到心跳，否则降级链路上修复不生效。"""
    from backend.app.services.llm import LLMProvider, RoutedProvider

    class Fake(LLMProvider):
        def __init__(self):
            self.last_delta_at = 0.0

        async def stream_chat(self, messages):
            yield 'x'

    primary = Fake()
    routed = RoutedProvider([primary, Fake()], 'research')
    assert routed.last_delta_at == 0.0
    primary.last_delta_at = 12345.0
    assert routed.last_delta_at == 12345.0, '路由层必须透传当前通道的存活时间'


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
