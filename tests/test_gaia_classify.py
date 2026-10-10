from pathlib import Path

from evals.gaia_classify import classify_task


def row(filename: str = "", tools: str = "") -> dict[str, object]:
    return {"task_id": "task-1", "Level": "1", "Question": "question", "file_name": filename, "Annotator Metadata": {"Tools": tools}}


def test_supported_document_and_image():
    assert classify_task(row("report.pdf"), Path("validation"))["status"] == "supported"
    assert classify_task(row("photo.png"), Path("validation"))["status"] == "supported"


def test_unsupported_media_and_special_formats():
    assert classify_task(row("voice.mp3"), Path("validation"))["status"] == "unsupported_audio"
    assert classify_task(row("clip.MOV"), Path("validation"))["status"] == "unsupported_video"
    assert classify_task(row("data.jsonld"), Path("validation"))["status"] == "unsupported_special_format"


def test_dynamic_browser_requirement_is_excluded_without_attachment():
    result = classify_task(row(tools="Google Street View"), Path("validation"))
    assert result["status"] == "unsupported_dynamic_web"


def test_nonordinary_spreadsheet_visual_structure_is_excluded():
    result = classify_task(
        row(
            "map.xlsx",
        )
        | {
            "Question": "This Excel file is a map. Green cells are plots of land and blue cells are blocked.",
        },
        Path("validation"),
    )
    assert result["status"] == "unsupported_document_parsing"
