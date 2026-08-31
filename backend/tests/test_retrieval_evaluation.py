from backend.app.services.rag.evaluation import compare_thresholds, evaluate_cases


def test_retrieval_metrics_include_recall_citations_and_rejection():
    cases = [
        {"id": "hit", "query": "known", "answerable": True, "relevant_ids": ["a"]},
        {"id": "none", "query": "unknown", "answerable": False, "relevant_ids": []},
    ]
    result = evaluate_cases(cases, lambda query, _k: ["a"] if query == "known" else [])
    assert result["metrics"]["recall_at_1"] == 1
    assert result["metrics"]["citation_correct_rate"] == 1
    assert result["metrics"]["no_answer_rejection_rate"] == 1
    assert compare_thresholds(result["metrics"], {"recall_at_1": 1})["passed"] is True


def test_retrieval_metrics_are_stratified_and_compact_dataset_expands():
    from scripts.evaluate_retrieval import expand_cases

    dataset = {"corpus": [{"id": "a", "queries": ["one", "two"], "strata": ["ocr"]}],
               "unanswerable_queries": ["none"]}
    cases = expand_cases(dataset)
    assert len(cases) == 3
    result = evaluate_cases(cases, lambda query, _k: ["a"] if query != "none" else [])
    assert result["by_stratum"]["ocr"]["recall_at_1"] == 1
    assert result["by_stratum"]["unanswerable"]["no_answer_rejection_rate"] == 1


def test_capacity_script_uses_isolated_temporary_database():
    from scripts.benchmark_large_library import run

    report = run(5, 4, 5)
    assert report["metrics"]["books"] == 5
    assert report["metrics"]["chunks"] == 20
    assert report["thresholds"]["passed"] is True


def test_explicitly_missing_library_material_is_refused_before_retrieval():
    from backend.app.services.rag.retriever import explicitly_out_of_scope

    assert explicitly_out_of_scope("未上传小说的最终结局是什么") is True
    assert explicitly_out_of_scope("当前库中某实验的原始数据链接") is True
    assert explicitly_out_of_scope("这篇已上传文献如何定义内部效度") is False
