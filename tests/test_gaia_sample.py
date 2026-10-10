from evals.gaia_sample import select_sample


def test_sample_is_supported_and_balances_levels_and_kinds():
    rows = [
        {"task_id": f"{level}-{kind}", "level": str(level), "status": "supported", "file_name": f"x.{ext}" if ext else ""}
        for level in (1, 2, 3)
        for kind, ext in (("text", ""), ("doc", "pdf"), ("image", "png"), ("extra", "txt"))
    ]
    rows.append({"task_id": "excluded", "level": "1", "status": "unsupported_audio", "file_name": "x.mp3"})
    sample = select_sample(rows, 9)
    assert len(sample) == 9
    assert {row["level"] for row in sample} == {"1", "2", "3"}
    assert all(row["status"] == "supported" for row in sample)
