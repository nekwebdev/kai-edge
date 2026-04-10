from __future__ import annotations

import logging
import sys
import types
import unittest
from unittest import mock

from kai_edge.config import build_edge_config
from kai_edge.wakeword import build_wakeword_detector


class FakePorcupineEngine:
    def __init__(self) -> None:
        self.sample_rate = 16000
        self.frame_length = 2
        self.deleted = False

    def process(self, _pcm: object) -> int:
        return 0

    def delete(self) -> None:
        self.deleted = True


class FakePorcupineModule:
    def __init__(self) -> None:
        self.engine = FakePorcupineEngine()
        self.last_kwargs: dict[str, object] | None = None

    def create(self, **kwargs: object) -> FakePorcupineEngine:
        self.last_kwargs = kwargs
        return self.engine


class FakeOpenWakeWordModel:
    last_kwargs: dict[str, object] | None = None
    last_instance: "FakeOpenWakeWordModel | None" = None

    def __init__(self, **kwargs: object) -> None:
        type(self).last_kwargs = kwargs
        self.sample_rate = 16000
        self.frame_length = 2
        self.closed = False
        type(self).last_instance = self

    def predict(self, _pcm: object) -> dict[str, float]:
        return {"fake_model": 0.8}

    def close(self) -> None:
        self.closed = True


class FakeNumpyModule:
    int16 = "int16"

    @staticmethod
    def frombuffer(frame: bytes, dtype: object) -> bytes:
        del dtype
        return frame


class WakeWordBuilderTests(unittest.TestCase):
    def test_build_wakeword_detector_dispatches_to_porcupine(self) -> None:
        fake_module = FakePorcupineModule()
        logger = logging.getLogger("test-wakeword-porcupine")
        config = build_edge_config(
            file_settings={
                "KAI_TRIGGER_MODE": "wakeword",
                "KAI_WAKEWORD_BACKEND": "porcupine",
                "KAI_WAKEWORD_ACCESS_KEY": "test-access-key",
                "KAI_WAKEWORD_BUILTIN_KEYWORD": "porcupine",
                "KAI_OBS_STATUS_FILE_ENABLED": "0",
            }
        )

        with mock.patch.dict(sys.modules, {"pvporcupine": fake_module}, clear=False):
            detector = build_wakeword_detector(config=config, logger=logger)

        self.assertEqual(detector.backend_name, "porcupine")
        self.assertEqual(detector.sample_rate, 16000)
        self.assertEqual(detector.frame_bytes, 4)
        self.assertTrue(detector.process_frame(frame=b"\x00\x00\x00\x00"))
        detector.close()
        self.assertTrue(fake_module.engine.deleted)
        self.assertEqual(
            fake_module.last_kwargs,
            {
                "access_key": "test-access-key",
                "keywords": ["porcupine"],
                "sensitivities": [0.5],
            },
        )

    def test_build_wakeword_detector_dispatches_to_openwakeword(self) -> None:
        logger = logging.getLogger("test-wakeword-openwakeword")
        config = build_edge_config(
            file_settings={
                "KAI_TRIGGER_MODE": "wakeword",
                "KAI_WAKEWORD_BACKEND": "openwakeword",
                "KAI_WAKEWORD_OPENWAKEWORD_MODEL_PATHS": "/opt/kai/models/a.onnx,/opt/kai/models/b.onnx",
                "KAI_WAKEWORD_OPENWAKEWORD_THRESHOLD": "0.7",
                "KAI_OBS_STATUS_FILE_ENABLED": "0",
            }
        )

        openwakeword_module = types.ModuleType("openwakeword")
        openwakeword_model_module = types.ModuleType("openwakeword.model")
        openwakeword_model_module.Model = FakeOpenWakeWordModel
        openwakeword_module.model = openwakeword_model_module
        numpy_module = types.ModuleType("numpy")
        numpy_module.int16 = FakeNumpyModule.int16
        numpy_module.frombuffer = FakeNumpyModule.frombuffer

        with mock.patch.dict(
            sys.modules,
            {
                "openwakeword": openwakeword_module,
                "openwakeword.model": openwakeword_model_module,
                "numpy": numpy_module,
            },
            clear=False,
        ):
            detector = build_wakeword_detector(config=config, logger=logger)

        self.assertEqual(detector.backend_name, "openwakeword")
        self.assertEqual(detector.sample_rate, 16000)
        self.assertEqual(detector.frame_bytes, 4)
        self.assertTrue(detector.process_frame(frame=b"\x00\x00\x00\x00"))
        detector.close()
        self.assertEqual(
            FakeOpenWakeWordModel.last_kwargs,
            {"wakeword_models": ["/opt/kai/models/a.onnx", "/opt/kai/models/b.onnx"]},
        )
        self.assertIsNotNone(FakeOpenWakeWordModel.last_instance)
        self.assertTrue(FakeOpenWakeWordModel.last_instance.closed)


if __name__ == "__main__":
    unittest.main()
