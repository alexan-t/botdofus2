"""Threads ONNX de RapidOCR : réglage optionnel, jamais imposé ; banc mesurable sans rien appliquer."""
from __future__ import annotations

import sys
import types

from combatbot.performance.ocr_benchmark import markdown_report, run_ocr_threads_benchmark
from combatbot.vision import tooltip


class FakeEngine:
    created: list[dict | None] = []

    def __init__(self, params=None) -> None:
        FakeEngine.created.append(params)

    def __call__(self, image, **_kwargs):
        return types.SimpleNamespace(txts=("Temple",), scores=(0.9,), boxes=None)


def test_threads_setting_is_optional_and_passed_to_onnx(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=FakeEngine))
    FakeEngine.created.clear()
    monkeypatch.delenv(tooltip.OCR_THREADS_ENV, raising=False)
    assert tooltip.ocr_threads_setting() is None
    tooltip.create_ocr_engine(None)
    assert FakeEngine.created[-1] is None                       # comportement historique inchangé
    monkeypatch.setenv(tooltip.OCR_THREADS_ENV, "2")
    assert tooltip.ocr_threads_setting() == 2
    tooltip.create_ocr_engine(2)
    assert FakeEngine.created[-1]["EngineConfig.onnxruntime.intra_op_num_threads"] == 2
    for invalid in ("0", "-3", "beaucoup"):
        monkeypatch.setenv(tooltip.OCR_THREADS_ENV, invalid)
        assert tooltip.ocr_threads_setting() is None


def test_engine_without_params_support_falls_back(monkeypatch) -> None:
    class Old:
        def __init__(self, *args, **kwargs) -> None:
            if kwargs:
                raise TypeError("params inconnu")

    monkeypatch.setitem(sys.modules, "rapidocr", types.SimpleNamespace(RapidOCR=Old))
    assert isinstance(tooltip.create_ocr_engine(3), Old)


def test_benchmark_measures_each_setting_without_applying_it() -> None:
    report = run_ocr_threads_benchmark(options=(None, 1), reads=2, factory=lambda threads: FakeEngine())
    assert report["verdict"] == "MEASURED" and [row["threads"] for row in report["rows"]] == ["défaut", 1]
    assert "DOFBOT_OCR_THREADS" in markdown_report(report)
