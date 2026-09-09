"""Bounded, full-scope reading for long research inputs."""
from __future__ import annotations

from sqlalchemy import func, select

from backend.app.models import Chunk


def scope_filter(book_ids, chapter_ids):
    predicates = [Chunk.book_id.in_(book_ids)]
    if chapter_ids:
        predicates.append(Chunk.chapter_id.in_(chapter_ids))
    return predicates


def scope_size(db, book_ids, chapter_ids):
    count, chars = db.execute(select(func.count(Chunk.id), func.sum(func.length(Chunk.content)))
                              .where(*scope_filter(book_ids, chapter_ids))).one()
    return int(count or 0), int(chars or 0)


def iter_evidence_batches(db, book_ids, chapter_ids, anchor_for, budget=12000):
    """Keyset reads close their transaction before yielding to a network call.

    Oversized chunks are split, never truncated. Each slice retains its source.
    """
    last_id = 0
    parts, refs, size, covered = [], set(), 0, 0
    while True:
        rows = db.execute(select(Chunk.id, Chunk.book_id, Chunk.chapter_id,
                                 Chunk.page_start, Chunk.page_end, Chunk.content)
                          .where(*scope_filter(book_ids, chapter_ids), Chunk.id > last_id)
                          .order_by(Chunk.id).limit(32)).mappings().all()
        db.rollback()
        if not rows:
            break
        for row in rows:
            last_id = row['id']
            text = str(row['content'] or '')
            anchor = anchor_for({**row, 'chunk_id': row['id']})
            width = budget - len(anchor) - 16
            for offset in range(0, len(text), width):
                body = text[offset:offset + width]
                part = f'[{anchor}]\n{body}'
                if parts and size + len(part) + 2 > budget:
                    yield '\n\n'.join(parts), refs, covered
                    parts, refs, size, covered = [], set(), 0, 0
                parts.append(part)
                refs.add(anchor)
                size += len(part) + 2
                covered += len(body)
    if parts:
        yield '\n\n'.join(parts), refs, covered


async def read_long_scope(db, provider, book_ids, chapter_ids, focus, anchor_for,
                          stream, progress):
    """Map every batch, then hierarchically reduce bounded source notes."""
    _, total = scope_size(db, book_ids, chapter_ids)
    db.rollback()
    summaries, allowed, processed = [], set(), 0
    for index, (body, refs, covered) in enumerate(
            iter_evidence_batches(db, book_ids, chapter_ids, anchor_for), 1):
        progress(processed, total, f'正在研读第 {index} 批（已处理 {processed}/{total} 字）')
        summary = await stream(provider, [
            {'role': 'system', 'content': '阅读这一批原始材料，提炼与问题有关的主张、推理、证据、冲突和反例。'
             '控制在1200字内；每条判断保留对应的原始[B…]锚点；未知不补造。只输出阅读笔记，不写最终文章。'},
            {'role': 'user', 'content': f'问题：{focus}\n原始材料：\n{body}'},
        ], '分批研读失败', on_progress=lambda _: progress(
            processed, total, f'正在研读第 {index} 批（已处理 {processed}/{total} 字）'))
        summaries.append(summary)
        allowed.update(refs)
        processed += covered
        progress(processed, total, f'已研读 {index} 批，覆盖 {processed}/{total} 字')
        # Compact incrementally, so the whole book's intermediate notes never
        # accumulate in one model request or grow without bound in memory.
        if sum(map(len, summaries)) > 24000:
            summaries = await reduce_notes(provider, summaries, stream, progress, processed, total)
    return '\n\n'.join(summaries), allowed, {'processed_chars': processed, 'total_chars': total}


async def reduce_notes(provider, notes, stream, progress, processed, total):
    compacted, group, size = [], [], 0
    # Split even a noncompliant model's oversized note without dropping its tail.
    pieces = [note[i:i + 10000] for note in notes for i in range(0, len(note), 10000)]
    async def compact(values):
        return await stream(provider, [
            {'role': 'system', 'content': '将已有阅读笔记综合为不超过1800字的证据地图，'
             '保留重要机制、共识、分歧、反例与逐字来源锚点。不要引入新事实。'},
            {'role': 'user', 'content': '\n\n'.join(values)},
        ], '证据综合失败', on_progress=lambda _: progress(processed, total, '正在综合分批阅读证据'))
    for note in pieces:
        if group and size + len(note) > 12000:
            compacted.append(await compact(group))
            group, size = [], 0
        group.append(note)
        size += len(note)
    if group:
        compacted.append(await compact(group))
    if sum(map(len, compacted)) >= sum(map(len, notes)):
        raise ValueError('模型未遵守证据压缩长度，请换用能遵循长度要求的研究模型后重试')
    if sum(map(len, compacted)) > 24000:
        return await reduce_notes(provider, compacted, stream, progress, processed, total)
    return compacted
