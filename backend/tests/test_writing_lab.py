"""写作实验室的硬边界、信息守恒与 Word 格式测试。"""
from pathlib import Path

import docx
import pytest

from backend.app.api.writing import OutputReviewReq, OutputUpdateReq, review_output, update_output
from backend.app.core.config import settings
from backend.app.core.database import SessionLocal
from backend.app.models import WritingOutput
from backend.app.services.writing_lab import (
    _replace_across_runs,
    _validate_replacement,
    apply_text_changes,
    create_word_output,
    text_blocks,
    validate_corpus,
)


def test_writing_dna_rejects_fewer_than_twenty_articles():
    with pytest.raises(ValueError, match="至少需要 20 篇"):
        validate_corpus(None, list(range(1, 20)))


def test_text_change_preserves_line_structure():
    source = "标题\n\n说白了，结果可能是 12%。\n末段\n"
    changes = [{"id": "line:2", "old": "说白了，", "new": "", "rule_ids": [9]}]
    assert apply_text_changes(source, changes) == "标题\n\n结果可能是 12%。\n末段\n"
    assert [item["id"] for item in text_blocks(source)] == ["line:0", "line:2", "line:3"]


def test_information_guard_rejects_number_and_qualifier_loss():
    with pytest.raises(ValueError, match="12%"):
        _validate_replacement("结果可能提高 12%", "结果可能提高")
    with pytest.raises(ValueError, match="限定词"):
        _validate_replacement("结果可能提高", "结果提高")


def test_cross_run_replacement_keeps_unaffected_run_formatting():
    document = docx.Document()
    paragraph = document.add_paragraph()
    first = paragraph.add_run("说白")
    first.bold = True
    second = paragraph.add_run("了，结论")
    second.italic = True
    assert _replace_across_runs(paragraph, "说白了，", "") is True
    assert paragraph.text == "结论"
    assert second.text == "结论"
    assert second.italic is True


def test_word_output_uses_stzhongsong_as_primary_font(tmp_path: Path):
    destination = tmp_path / "writing.docx"
    create_word_output("测试", "# 标题\n\n正文", destination)
    document = docx.Document(destination)
    normal_xml = document.styles["Normal"]._element.xml
    assert "华文中宋" in normal_xml
    assert document.paragraphs[0].text == "测试"
    assert "华文中宋" in document.styles["Title"]._element.xml
    assert destination.stat().st_size > 0


def test_ai_tone_review_can_restore_or_apply_each_change():
    db = SessionLocal()
    try:
        row = WritingOutput(kind="ai_tone", title="审阅稿", input_type="text",
                            source_text="说白了，结果可能提高 12%。", output_text="结果可能提高 12%。",
                            audit_json='{"changes":[{"id":"line:0","old":"说白了，","new":"","rule_ids":[9]}]}')
        db.add(row); db.commit(); db.refresh(row)
        restored = review_output(row.id, OutputReviewReq(accepted_indexes=[]), db)
        assert restored["output_text"] == "说白了，结果可能提高 12%。"
        filename = db.get(WritingOutput, row.id).output_file_path
        applied = review_output(row.id, OutputReviewReq(accepted_indexes=[0]), db)
        assert applied["output_text"] == "结果可能提高 12%。"
        assert applied["audit"]["accepted_indexes"] == [0]
        assert db.get(WritingOutput, row.id).output_file_path == filename
    finally:
        db.close()


def test_imitation_output_can_be_edited_and_regenerates_same_word_file():
    db = SessionLocal()
    try:
        row = WritingOutput(kind="imitation", title="初稿", input_type="text",
                            source_text="", output_text="原内容", audit_json="{}")
        db.add(row); db.commit(); db.refresh(row)
        first = update_output(row.id, OutputUpdateReq(title="修订稿", output_text="修订内容"), db)
        filename = db.get(WritingOutput, row.id).output_file_path
        second = update_output(row.id, OutputUpdateReq(title="再修订", output_text="最终内容"), db)
        assert first["audit"]["user_edited"] is True
        assert second["output_text"] == "最终内容"
        assert db.get(WritingOutput, row.id).output_file_path == filename
    finally:
        db.close()


def test_docx_review_replays_selected_changes_from_original_format():
    source_dir = settings.writing_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    source = source_dir / "review-format-source.docx"
    document = docx.Document()
    paragraph = document.add_paragraph()
    first = paragraph.add_run("说白"); first.bold = True
    second = paragraph.add_run("了，结论"); second.italic = True
    document.save(source)
    db = SessionLocal()
    try:
        row = WritingOutput(kind="ai_tone", title="Word审阅稿", input_type="docx",
                            source_text=None, output_text="结论", source_file_path=source.name,
                            audit_json='{"changes":[{"id":"p:0","old":"说白了，","new":"","rule_ids":[9]}]}')
        db.add(row); db.commit(); db.refresh(row)
        result = review_output(row.id, OutputReviewReq(accepted_indexes=[0]), db)
        saved = docx.Document(settings.writing_dir / "outputs" / db.get(WritingOutput, row.id).output_file_path)
        assert result["audit"]["applied"] == 1
        assert saved.paragraphs[0].text == "结论"
        assert saved.paragraphs[0].runs[-1].italic is True
    finally:
        db.close()
