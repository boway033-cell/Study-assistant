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


def test_capacity_script_uses_isolated_temporary_database():
    from scripts.benchmark_large_library import run

    report = run(5, 4, 5)
    assert report["metrics"]["books"] == 5
    assert report["metrics"]["chunks"] == 20
    assert report["thresholds"]["passed"] is True
