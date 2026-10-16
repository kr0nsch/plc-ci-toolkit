"""Политики workflow plc-ci-toolkit как тесты: закрепление actions, события запуска, guard, таймаут.

Синтаксис workflow проверяет actionlint (CI, задание actionlint); здесь — свойства, от которых зависит
безопасность self-hosted раннера (docs/runner_security.md) и которые actionlint не проверяет.
YAML разбирается по строкам: файлы workflow пишутся в одном стиле (отступ два пробела).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
EXAMPLE = ROOT / "examples" / "consumer-ci.yml"
REUSABLE = ("plc-static.yml", "plc-codesys.yml")

USES_RE = re.compile(r"^\s*(?:-\s+)?uses:\s*(?P<ref>\S+)(?:\s+#\s*(?P<comment>.*))?$")
PINNED_RE = re.compile(r"^(?P<action>[\w.-]+/[\w./-]+)@(?P<sha>[0-9a-f]{40})$")


def text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def block(source: str, header: str, indent: int) -> str:
    """Строки блока `header:` с отступом indent (до следующего ключа того же уровня)."""
    lines = source.splitlines()
    prefix = " " * indent + header + ":"
    start = next(i for i, line in enumerate(lines) if line.rstrip() == prefix or line.startswith(prefix + " "))
    out = [lines[start]]
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(" " * (indent + 1)) and not line.lstrip().startswith("#"):
            break
        out.append(line)
    return "\n".join(out)


def run_scripts(job: str) -> list[str]:
    """Тексты run-скриптов задания: блочные скаляры `run: |` и однострочные `run: команда`."""
    scripts: list[str] = []
    lines = job.splitlines()
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)(?:-\s+)?run:\s*\|\s*$", lines[i])
        if not match:
            inline = re.match(r"^\s*(?:-\s+)?run:\s*(?P<script>[^|>\s].*)$", lines[i])
            if inline:
                scripts.append(inline.group("script"))
            i += 1
            continue
        base = len(match.group(1))
        body: list[str] = []
        i += 1
        while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip()) > base):
            body.append(lines[i])
            i += 1
        scripts.append("\n".join(body))
    return scripts


def all_uses() -> list[tuple[str, str, str | None]]:
    found = []
    for path in [*sorted(WORKFLOWS.glob("*.yml")), EXAMPLE]:
        for line in path.read_text(encoding="utf-8").splitlines():
            match = USES_RE.match(line)
            if match:
                found.append((path.name, match.group("ref"), match.group("comment")))
    return found


def test_actions_are_pinned_by_full_sha_with_version_comment() -> None:
    uses = all_uses()
    assert uses
    shas: dict[str, set[str]] = {}
    for name, ref, comment in uses:
        if ref.startswith("./"):
            continue  # локальный переиспользуемый workflow того же коммита
        if "/.github/workflows/" in ref:
            assert name == EXAMPLE.name, f"{name}: внешний workflow {ref}"
            assert ref.endswith("@v1"), ref  # потребитель вызывает plc-ci-toolkit по основной версии
            continue
        match = PINNED_RE.match(ref)
        assert match, f"{name}: {ref} не закреплён по полному SHA коммита"
        assert comment and re.match(r"v\d+\.\d+\.\d+$", comment), f"{name}: у {ref} нет комментария версии"
        shas.setdefault(match.group("action"), set()).add(match.group("sha"))
    for action, values in shas.items():
        assert len(values) == 1, f"{action}: разные SHA в разных workflow: {values}"


@pytest.mark.parametrize("name", REUSABLE)
def test_reusable_workflows_are_call_only_with_explicit_permissions(name: str) -> None:
    source = text(name)
    on = block(source, "on", 0)
    assert re.findall(r"^  (\w[\w-]*):", on, re.MULTILINE) == ["workflow_call"]
    jobs = block(source, "jobs", 0)
    job_names = re.findall(r"^  (\w[\w-]*):\s*$", jobs, re.MULTILINE)
    assert job_names
    for job in job_names:
        assert re.search(r"^    permissions:", block(jobs, job, 2), re.MULTILINE), f"{name}: {job} без permissions"


@pytest.mark.parametrize("name", sorted(p.name for p in WORKFLOWS.glob("*.yml")))
def test_run_scripts_take_inputs_through_env_not_expressions(name: str) -> None:
    # выражение в тексте скрипта подставляется до запуска оболочки — путь к внедрению команд
    for script in run_scripts(text(name)):
        assert "${{" not in script, f"{name}: выражение внутри run-скрипта:\n{script}"


def test_codesys_guard_runs_first_on_github_hosted_runner() -> None:
    jobs = block(text("plc-codesys.yml"), "jobs", 0)
    guard = block(jobs, "guard", 2)
    codesys = block(jobs, "codesys", 2)
    assert "runs-on: ubuntu-latest" in guard
    assert "permissions: {}" in guard
    assert re.search(r"^    needs: guard$", codesys, re.MULTILINE)
    script = run_scripts(guard)[0]
    # список разрешённых событий, а не запрещённых: pull_request* и прочие отклоняются
    assert re.search(r"push \| workflow_dispatch \| schedule\) ;;", script)
    assert 'IS_FORK" = "true"' in script
    assert "-ge 110" in script


def test_codesys_timeout_concurrency_and_ascii_powershell() -> None:
    source = text("plc-codesys.yml")
    timeout = block(block(block(source, "on", 0), "inputs", 4), "timeout-minutes", 6)
    default = int(re.search(r"default:\s*(\d+)", timeout).group(1))  # type: ignore[union-attr]
    assert default < 110  # демо-режим рантайма — 2 ч
    codesys = block(block(source, "jobs", 0), "codesys", 2)
    concurrency = block(codesys, "concurrency", 4)
    assert "cancel-in-progress: false" in concurrency
    assert "queue: max" in concurrency
    assert "-ExecutionPolicy Bypass" in codesys
    # раннер пишет скрипт в UTF-8 без BOM, PowerShell 5.1 читает его как ANSI (docs/ASSUMPTIONS.md, W6)
    for script in run_scripts(codesys):
        bad = [line for line in script.splitlines() if not line.isascii()]
        assert not bad, f"не-ASCII в PowerShell-скрипте: {bad}"


def test_codesys_never_triggered_by_pull_requests() -> None:
    own = block(text("codesys.yml"), "on", 0)
    assert "pull_request" not in own
    assert re.findall(r"^  (\w[\w-]*):", own, re.MULTILINE) == ["push", "workflow_dispatch"]
    example = EXAMPLE.read_text(encoding="utf-8")
    assert re.findall(r"^  (\w[\w-]*):", block(example, "on", 0), re.MULTILINE) == [
        "push",
        "pull_request",
        "workflow_dispatch",
    ]
    job = block(block(example, "jobs", 0), "codesys", 2)
    condition = " ".join(block(job, "if", 4).split())
    assert "vars.PLC_CODESYS_RUNNER == 'true'" in condition
    assert "github.event_name == 'workflow_dispatch'" in condition
    assert "github.event_name == 'push'" in condition
    assert "pull_request" not in condition


def test_toolkit_scripts_are_checked_out_from_the_workflow_commit() -> None:
    for name in REUSABLE:
        source = text(name)
        assert "repository: ${{ inputs.toolkit-repository || job.workflow_repository }}" in source
        assert "ref: ${{ inputs.toolkit-ref || job.workflow_sha }}" in source
        assert source.count("persist-credentials: false") == 2  # оба checkout без токена в .git/config


def test_codesys_shell_passes_exit_codes_through() -> None:
    codesys = block(block(text("plc-codesys.yml"), "jobs", 0), "codesys", 2)
    match = re.search(r"^\s+shell:\s*(?P<shell>.+)$", codesys, re.MULTILINE)
    assert match
    shell = match.group("shell").strip()
    # имя оболочки — powershell: раннер добавляет $ErrorActionPreference и выход по $LASTEXITCODE
    assert shell.split()[0] == "powershell"
    # -File, а не -Command ". '{0}'": с -Command код выхода неуспешного шага всегда 1 (ASSUMPTIONS W6);
    # путь в кавычках — каталог раннера может содержать пробелы
    assert shell.endswith('-File "{0}"')
    assert "-Command" not in shell


def test_static_pip_cache_requires_requirements_file() -> None:
    source = text("plc-static.yml")
    setup = source[source.index("uses: actions/setup-python@") :]
    setup = setup[: setup.index("\n      - ")]
    # без файла зависимостей setup-python с cache: pip падает до проверок и без сводки (W11)
    assert "hashFiles(format('{0}/{1}', inputs.working-directory, inputs.requirements)) != ''" in setup


def test_consumer_example_keeps_pending_main_runs() -> None:
    concurrency = block(EXAMPLE.read_text(encoding="utf-8"), "concurrency", 0)
    # общая группа по ссылке — только у pull request; у push в main своя группа на запуск, иначе
    # следующий push отменял бы ожидающий запуск, и коммит остался бы без прогона в CODESYS
    assert "github.event_name == 'pull_request' && github.ref || github.run_id" in concurrency
    assert "cancel-in-progress: ${{ github.event_name == 'pull_request' }}" in concurrency


def test_no_secrets_reach_the_self_hosted_runner() -> None:
    # workflow не объявляют секретов, пример и самопроверка их не передают (secrets: inherit и т. п.)
    for source in (text("plc-codesys.yml"), text("codesys.yml"), EXAMPLE.read_text(encoding="utf-8")):
        assert not re.search(r"^\s*secrets:", source, re.MULTILINE)
        assert "secrets." not in source
