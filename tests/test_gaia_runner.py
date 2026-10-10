from evals.gaia_runner import answer_matches, normalize_answer


def test_normalize_answer_and_match_short_final_line():
    assert normalize_answer("```text\n  42. \n```") == "42"
    assert answer_matches("The answer is:\n42", "42")
    assert not answer_matches("41", "42")


def test_answer_matches_common_markdown_and_label_formats():
    assert answer_matches("该供应商的类型是：**Finance**。", "Finance")
    assert answer_matches("**Answer: 3**", "3")
    assert answer_matches("最终答案：**90**。", "90")
    assert answer_matches("8,29,22,1,8,26", "8, 29, 22, 1, 8, 26")


def test_answer_match_does_not_accept_unlabeled_explanation_numbers():
    assert not answer_matches("The explanation mentions 3, but the final answer is 4.", "3")
