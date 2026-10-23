from pathlib import Path

import pytest
import yaml

from ..conftest import make_msg

CASES = yaml.safe_load((Path(__file__).parents[1] / "fixtures" / "extraction_cases.yaml").read_text("utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[c["input"][:12] for c in CASES])
def test_extraction_case(harness, case):
    harness.provider.outputs = [{"fields": case["llm"], "missing": [], "confidence": {}}]
    harness.service.handle(make_msg(f"{case['role']} {case['input']}"))
    written = harness.api.records[0]["fields"]
    for k, v in case["expected"].items():
        assert written[k] == v, k
