from pathlib import Path

from winescan.config import PROJECT_ROOT, project_root


def test_project_root_is_repository_when_running_from_sources():
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert (PROJECT_ROOT / "src" / "winescan" / "config.py").is_file()


def test_project_root_falls_back_to_cwd_for_installed_package(tmp_path):
    venv = tmp_path / "opt" / "venv" / "lib" / "python3.12"
    venv.mkdir(parents=True)
    workdir = tmp_path / "app"
    (workdir / "configs").mkdir(parents=True)
    (workdir / "pyproject.toml").touch()

    assert project_root(venv, workdir) == workdir
    assert project_root(Path(PROJECT_ROOT), workdir) == PROJECT_ROOT
