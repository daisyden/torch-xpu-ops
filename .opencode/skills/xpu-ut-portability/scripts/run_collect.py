import sys
import torch.testing._internal.common_device_type as cdt
if getattr(cdt, "TEST_XPU", False) and cdt.XPUTestBase not in cdt.device_type_test_bases:
    cdt.device_type_test_bases.append(cdt.XPUTestBase)
import pytest
sys.exit(pytest.main(["--collect-only", "-q", "-p", "no:cacheprovider"] + sys.argv[1:]))
