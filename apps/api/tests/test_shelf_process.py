import asyncio

from app.shelf_process import shelf_lifespan


def test_disabled_does_not_launch_process(monkeypatch):
    monkeypatch.delenv('VINCHIK_SHELF_LOCAL', raising=False)
    async def unexpected(*args, **kwargs):
        raise AssertionError('unexpected subprocess')
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', unexpected)
    async def scenario():
        async with shelf_lifespan(None):
            await asyncio.sleep(0)
    asyncio.run(scenario())


def test_enabled_launches_isolated_cpu_service_and_stops_child(monkeypatch):
    monkeypatch.setenv('VINCHIK_SHELF_LOCAL', '1')
    monkeypatch.setenv('SHELF_PYTHON', '/opt/somelye/shelf-venv/bin/python')
    monkeypatch.setenv('SHELF_DEVICE', 'cpu')
    monkeypatch.setenv('SHELF_OPENBLAS_CORETYPE', 'Sandybridge')
    calls = []
    async def scenario():
        class Process:
            returncode = None
            stopped = asyncio.Event()
            async def wait(self):
                await self.stopped.wait()
                return self.returncode
            def terminate(self):
                self.returncode = 0
                self.stopped.set()
        process = Process()
        async def launch(*args, **kwargs):
            calls.append((args, kwargs))
            return process
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', launch)
        async with shelf_lifespan(None):
            await asyncio.sleep(0)
            assert process.returncode is None
        assert process.returncode == 0
    asyncio.run(scenario())
    args, kwargs = calls[0]
    assert args[0] == '/opt/somelye/shelf-venv/bin/python'
    assert '127.0.0.1' in args and '8086' in args
    assert kwargs['env']['SHELF_DEVICE'] == 'cpu'
    assert kwargs['env']['OPENBLAS_CORETYPE'] == 'Sandybridge'
    assert kwargs['env']['PYTHONPATH'].endswith('/apps/shelf-finder/server/src')
