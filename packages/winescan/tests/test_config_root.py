from pathlib import Path

from winescan.config import PROJECT_ROOT, project_root


def test_project_root_is_repository_when_running_from_sources():
    """Проверяется свойство, а не раскладка: рядом с корнем лежит и pyproject, и сам пакет.

    Раскладки две — отдельный репозиторий (`src/winescan/`) и монорепо сервиса
    (`packages/winescan/winescan/`); привязка теста к первой ломала бы перенос пакета."""
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    package = PROJECT_ROOT / "src" / "winescan" / "config.py"
    assert package.is_file() or (PROJECT_ROOT / "winescan" / "config.py").is_file()


def test_project_root_falls_back_to_cwd_for_installed_package(tmp_path):
    venv = tmp_path / "opt" / "venv" / "lib" / "python3.12"
    venv.mkdir(parents=True)
    workdir = tmp_path / "app"
    (workdir / "configs").mkdir(parents=True)
    (workdir / "pyproject.toml").touch()

    assert project_root(venv, workdir) == workdir
    assert project_root(Path(PROJECT_ROOT), workdir) == PROJECT_ROOT
