import importlib.util
import os

import pytest

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="session")
def ocr_lote():
    """Carrega src/ocr-lote.py (o hífen no nome impede o import comum)."""
    spec = importlib.util.spec_from_file_location("ocr_lote", os.path.join(RAIZ, "src", "ocr-lote.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def data_dir():
    return os.path.join(RAIZ, "data")
