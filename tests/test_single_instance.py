import os
import uuid

import pytest

from clickforge_core import claim_single_instance


@pytest.mark.skipif(os.name != "nt", reason="the claim is a Windows mutex")
def test_second_claim_is_refused_until_the_first_lets_go():
    name = f"Local\\ClickForgeTest-{uuid.uuid4().hex}"
    release = claim_single_instance(name)
    assert release is not None
    assert claim_single_instance(name) is None  # two copies would multiply every click twice
    release()
    again = claim_single_instance(name)
    assert again is not None
    again()
