"""Real 122-point regression: RANSAC SIGILL on Common KVM without core override."""
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest


def test_cpu_ransac_with_explicit_blas_core():
    pytest.importorskip('cv2')
    pytest.importorskip('numpy')
    if platform.machine().lower() not in ('x86_64', 'amd64'):
        pytest.skip('Haswell core applies only to x86_64')
    fixture = Path(__file__).parent / 'fixtures/kvm-homography.json'
    code = '''
import json,sys,cv2,numpy as np
r=json.load(open(sys.argv[1]))
h,mask=cv2.findHomography(np.asarray(r['src'],dtype=np.float32),np.asarray(r['dst'],dtype=np.float32),cv2.RANSAC,3,maxIters=2000,confidence=.995)
assert h is not None and mask is not None and int(mask.sum()) >= 75
'''
    result = subprocess.run([sys.executable, '-c', code, str(fixture)],
                            env={**os.environ, 'OPENBLAS_CORETYPE': 'Haswell'},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
