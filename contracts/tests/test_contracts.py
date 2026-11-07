import re

from studio_contracts import API_VERSION


def test_api_version_is_major_minor() -> None:
    assert re.fullmatch(r"\d+\.\d+", API_VERSION)
    assert API_VERSION.split(".")[0] == "1"
