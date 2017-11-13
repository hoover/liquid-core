import os
from pathlib import Path
import pytest
from django.test.utils import override_settings
from django.conf import settings
from liquidcore.config import system, agent


@pytest.fixture(autouse=True)
def mock_job_options(monkeypatch):
    value = {}

    def set_value(new_value):
        nonlocal value
        value = new_value

    monkeypatch.setattr(system, 'get_configuration', lambda: value)

    return set_value


MOCK_LIQUID_CORE_CONFIG = """\
#!/usr/bin/env python3
print("hello from mock setup")
"""


MOCK_FAIL_LIQUID_CORE_CONFIG = """\
#!/usr/bin/env python3
raise RuntimeError("please just die.")
"""


@pytest.fixture(autouse=True)
def setup(tmpdir, monkeypatch):
    mock_setup = Path(str(tmpdir.mkdir('setup'))) / 'mock-setup'

    class setup:
        def mock(content=None, succeeds=True):
            by_outcome = {
                True: MOCK_LIQUID_CORE_CONFIG,
                False: MOCK_FAIL_LIQUID_CORE_CONFIG,
            }
            with mock_setup.open('w', encoding='utf8') as f:
                f.write(content or by_outcome[succeeds])
            mock_setup.chmod(0o755)

    setup.mock()
    setup.core_var_dir = Path(str(tmpdir.mkdir('var')))

    with override_settings():
        settings.LIQUID_CORE_VAR = str(setup.core_var_dir)
        settings.LIQUID_SETUP_RECONFIGURE = str(mock_setup)

        yield setup


def test_run_one_job():
    job = system.reconfigure_system()
    job.wait()


def test_detect_failed_job(setup):
    setup.mock(succeeds=False)
    job = system.reconfigure_system()

    with pytest.raises(agent.JobFailed):
        job.wait()


def test_failed_job_marks_system_as_broken(setup):
    setup.mock(succeeds=False)

    def reconfigure(**kwargs):
        return system.reconfigure_system(**kwargs).wait()

    with pytest.raises(agent.JobFailed):
        reconfigure()

    # system is in "broken" state; jobs will fail until we run "repair"

    setup.mock(succeeds=True)

    with pytest.raises(agent.JobFailed):
        reconfigure()

    with pytest.raises(agent.JobFailed):
        reconfigure()

    reconfigure(repair=True)
    # system is repaired; next jobs will run happily.

    reconfigure()
    reconfigure()


TEST_CONCURRENCY_WHOAMI = """\
#!/usr/bin/env python3
import time
import sys
import json

vars = json.load(sys.stdin)['vars']
prefix = vars['prefix']
delay = vars['delay']

def stamp_time(n):
    filename = '{}{}.txt'.format(prefix, n)
    with open(filename, 'w', encoding='utf8') as f:
        print(time.time(), file=f)

stamp_time(0)
print('sleeping', delay)
time.sleep(delay)
stamp_time(1)
"""


DELAY_VALUES = [3]
if os.environ.get('AGENT_LOCKING_STRESSTEST'):
    DELAY_VALUES = [0, 3, 5, 1, 3, 5]

@pytest.mark.parametrize('delay', DELAY_VALUES)
@pytest.mark.skip(reason="old jobs don't run after new jobs")
def test_concurrency(setup, mock_job_options, delay):
    setup.mock(TEST_CONCURRENCY_WHOAMI)

    mock_job_options({
        'prefix': '{}/a'.format(setup.core_var_dir),
        'delay': delay,
    })
    job1 = system.reconfigure_system()
    mock_job_options({
        'prefix': '{}/b'.format(setup.core_var_dir),
        'delay': delay,
    })
    job2 = system.reconfigure_system()

    job1.wait()
    job2.wait()

    with job1.open_logfile() as f: print(f.read())
    with job2.open_logfile() as f: print(f.read())

    def t(name):
        path = setup.core_var_dir / (name + '.txt')
        with path.open(encoding='utf8') as f:
            return float(f.read())

    (a0, a1, b0, b1) = t('a0'), t('a1'), t('b0'), t('b1')
    assert (a0 < a1 < b0 < b1) or (b0 < b1 < a0 < a1)
