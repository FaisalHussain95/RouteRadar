"""S09: the scheduled pipeline on the host.

Two independent things are under test. `deploy/*.service|.timer` are templates
(`@REPO@`, `@UV@`) that `deploy/install.sh` renders into the calling user's unit
directory, so the units are checked *as installed* — rendering is part of what can
break. And `deploy/run-pipeline.sh`, the chain the service runs, is exercised with a
stub `uv` and a stub `push-data.sh`: the point of the chain is which steps do *not* run
after a failure, which reading the file cannot prove.
"""

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
SERVICE = "flight-detective-pipeline.service"
TIMER = "flight-detective-pipeline.timer"

# systemd-analyze ships with systemd; a container without it cannot check unit files at
# all. The host this pipeline runs on has it, and so does the CI image.
needs_systemd = pytest.mark.skipif(
    shutil.which("systemd-analyze") is None, reason="systemd-analyze is not installed"
)


@dataclass(frozen=True)
class Install:
    unit_dir: Path
    output: str

    def unit(self, name: str) -> str:
        return self.unit_dir.joinpath(name).read_text()


@pytest.fixture
def installed(tmp_path: Path) -> Install:
    """Run the real `deploy/install.sh` into a throwaway XDG_CONFIG_HOME and return what
    it wrote and said. `systemctl` is stubbed: enabling the timer is the one thing the
    installer does that reaches outside the directory it is given."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "systemctl.log"
    stub = fake_bin / "systemctl"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\nexit 0\n')
    stub.chmod(0o755)
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "config"))
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["bash", str(DEPLOY / "install.sh")], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # The installer must actually schedule the timer, not just write files.
    assert "enable --now flight-detective-pipeline.timer" in log.read_text()
    return Install(tmp_path / "config" / "systemd" / "user", result.stdout + result.stderr)


def _settings(text: str) -> dict[str, str]:
    """Unit file to a key -> value dict. Good enough: no key repeats in these two."""
    return dict(
        line.split("=", 1)
        for line in text.splitlines()
        if "=" in line and not line.startswith(("#", "["))
    )


# --- Acceptance: `systemd-analyze --user verify` passes on both units -----------------


@needs_systemd
@pytest.mark.parametrize("unit", [SERVICE, TIMER])
def test_installed_units_verify(installed: Install, unit: str) -> None:
    result = subprocess.run(
        ["systemd-analyze", "--user", "verify", str(installed.unit_dir / unit)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # A host without a system bus (a CI runner, a container) prints one line about it
    # before verifying the unit offline; that is the host's state, not a unit problem.
    complaints = [
        line for line in result.stderr.splitlines() if "Failed to connect to system bus" not in line
    ]
    assert complaints == []


def test_rendering_leaves_no_placeholder(installed: Install) -> None:
    for unit in (SERVICE, TIMER):
        assert "@" not in installed.unit(unit)


# --- Acceptance: the service pins the absolute uv path --------------------------------


def test_service_pins_an_absolute_uv_path(installed: Install) -> None:
    settings = _settings(installed.unit(SERVICE))
    uv = Path(settings["Environment"].removeprefix("FD_UV="))
    assert uv.is_absolute() and uv.exists(), uv
    assert uv == Path(shutil.which("uv") or "")
    # Everything else the service names is absolute too: systemd searches no PATH and
    # sources no shell profile.
    assert Path(settings["ExecStart"]).is_absolute()
    assert Path(settings["WorkingDirectory"]) == REPO


def test_the_service_orders_on_nothing_the_user_manager_cannot_load(
    installed: Install,
) -> None:
    settings = _settings(installed.unit(SERVICE))  # directives, not comments
    # `Wants=`/`After=network-online.target` is the obvious way to write "wait for the
    # network" and a silent no-op in a user manager, which has no such target and says
    # nothing about it — not in the journal and not in `systemd-analyze verify`. The
    # boot-time wait is on the system manager instead.
    assert "network-online.target" not in " ".join(settings.values())
    assert settings["ExecStartPre"] == "-/usr/bin/systemctl is-system-running --wait"


def test_timer_fires_daily_at_0630_paris(installed: Install) -> None:
    assert _settings(installed.unit(TIMER))["OnCalendar"] == ("*-*-* 06:30:00 Europe/Paris")


@needs_systemd
def test_the_calendar_expression_is_one_systemd_understands(installed: Install) -> None:
    calendar = _settings(installed.unit(TIMER))["OnCalendar"]
    result = subprocess.run(
        ["systemd-analyze", "calendar", calendar], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "06:30:00 Europe/Paris" in result.stdout


# --- Acceptance: a failing step stops the chain ---------------------------------------

# `uv run fd <step> …` and `deploy/push-data.sh` are replaced by stubs appending their
# arguments to $FD_LOG, so the assertions are on which steps ran, in what order, with
# what options.
STUB_UV = """#!/bin/sh
echo "$*" >> "$FD_LOG"
if [ "$3" = "${FD_FAIL_STEP:-}" ]; then exit "${FD_FAIL_CODE:-1}"; fi
exit 0
"""
STUB_PUSH = """#!/bin/sh
echo "push-data $*" >> "$FD_LOG"
exit "${FD_PUSH_CODE:-0}"
"""


@dataclass(frozen=True)
class Run:
    returncode: int
    calls: list[str]  # one line per stub invocation, arguments and all
    output: str

    @property
    def steps(self) -> list[str]:
        """`run fd tag-dates --path x` -> `tag-dates`; `push-data …` -> `push-data`."""
        return [c.split()[2] if c.startswith("run fd ") else c.split()[0] for c in self.calls]

    def call(self, step: str) -> str:
        return next(c for c in self.calls if c.split()[2:3] == [step])


@pytest.fixture
def pipeline(tmp_path: Path) -> Callable[..., Run]:
    """A callable running the real `run-pipeline.sh` in a copy of `deploy/`, with the
    environment the unit sets (`FD_UV`) plus whatever the test overrides."""
    deploy = tmp_path / "repo" / "deploy"
    deploy.mkdir(parents=True)
    shutil.copy(DEPLOY / "run-pipeline.sh", deploy / "run-pipeline.sh")
    (deploy / "push-data.sh").write_text(STUB_PUSH)
    uv = tmp_path / "uv"
    uv.write_text(STUB_UV)
    for path in (deploy / "run-pipeline.sh", deploy / "push-data.sh", uv):
        path.chmod(0o755)
    log = tmp_path / "steps.log"

    def run(**overrides: str) -> Run:
        log.unlink(missing_ok=True)
        env = dict(os.environ, FD_UV=str(uv), FD_LOG=str(log), **overrides)
        result = subprocess.run(
            ["bash", str(deploy / "run-pipeline.sh")], env=env, capture_output=True, text=True
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return Run(result.returncode, calls, result.stdout + result.stderr)

    return run


ALL_STEPS = ["ingest", "tag-dates", "news-ingest", "export-site", "push-data"]


def test_a_clean_run_runs_every_step_in_order(pipeline: Callable[..., Run]) -> None:
    run = pipeline()
    assert run.steps == ALL_STEPS
    assert run.returncode == 0


def test_a_failed_ingest_stops_before_anything_is_exported_or_pushed(
    pipeline: Callable[..., Run],
) -> None:
    run = pipeline(FD_FAIL_STEP="ingest", FD_FAIL_CODE="1")
    assert run.steps == ["ingest"]
    assert run.returncode == 1


def test_a_misconfigured_ingest_stops_the_chain_and_keeps_its_exit_code(
    pipeline: Callable[..., Run],
) -> None:
    # Exit 2 is `fd ingest`'s "SERPAPI_KEY is not set": nothing was even attempted.
    run = pipeline(FD_FAIL_STEP="ingest", FD_FAIL_CODE="2")
    assert run.steps == ["ingest"]
    assert run.returncode == 2


def test_a_partial_ingest_still_tags_exports_and_pushes(pipeline: Callable[..., Run]) -> None:
    # Exit 3 means some (route, horizon) cells failed and the rest were stored. The PRD
    # budgets < 2 % missing cells, so such a day is still worth exporting.
    run = pipeline(FD_FAIL_STEP="ingest", FD_FAIL_CODE="3")
    assert run.steps == ALL_STEPS
    assert run.returncode == 0
    assert "partial" in run.output


@pytest.mark.parametrize(
    ("failing", "expected"),
    [
        ("tag-dates", ["ingest", "tag-dates"]),
        ("news-ingest", ["ingest", "tag-dates", "news-ingest"]),
        ("export-site", ["ingest", "tag-dates", "news-ingest", "export-site"]),
    ],
)
def test_a_failing_middle_step_never_reaches_push(
    pipeline: Callable[..., Run], failing: str, expected: list[str]
) -> None:
    run = pipeline(FD_FAIL_STEP=failing, FD_FAIL_CODE="1")
    assert run.steps == expected
    assert run.returncode != 0


def test_a_failing_push_keeps_its_exit_code(pipeline: Callable[..., Run]) -> None:
    run = pipeline(FD_PUSH_CODE="4")
    assert run.steps == ALL_STEPS
    assert run.returncode == 4


def test_the_daily_run_queries_the_real_provider_over_the_prd_horizons(
    pipeline: Callable[..., Run],
) -> None:
    ingest = pipeline().call("ingest")
    assert "--provider serpapi" in ingest
    assert "--horizons 14,30,60,90,120,180" in ingest


def test_provider_and_horizons_can_be_overridden_by_the_unit(
    pipeline: Callable[..., Run],
) -> None:
    run = pipeline(FD_PROVIDER="fake", FD_HORIZONS="30,90")
    assert "--provider fake" in run.call("ingest")
    assert "--horizons 30,90" in run.call("ingest")


# --- Acceptance: README section on reading the journal --------------------------------


def test_readme_documents_the_journal_and_the_search_budget() -> None:
    readme = REPO.joinpath("README.md").read_text()
    assert "journalctl --user -u flight-detective-pipeline" in readme
    # The default grid is 36 searches a day; the number a reader needs before enabling
    # the timer is what that costs per month against SerpApi's plans.
    assert "36 searches" in readme
    assert "serpapi.com/pricing" in readme
