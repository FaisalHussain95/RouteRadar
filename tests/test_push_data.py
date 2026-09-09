"""S16: publishing the export, and the deploy chain around it.

`deploy/push-data.sh` is exercised as a real script against a real git repository with a
real (bare, on-disk) remote — the interesting behaviour is all in git's index and history,
which reading the file cannot prove. The push goes over a stub `ssh` that logs its
arguments and then runs the remote command locally, so the deploy-key wiring is checked
without a network and without a key that exists anywhere.

The rest is the two ends the script joins: which pushes `deploy-site.yml` rebuilds on, and
whether the README and installer still describe a chain that stops short.
"""

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "deploy"
DATA = "data/site/dashboard.json"

# git invokes `ssh [options] <host> <remote command>`; running that last argument locally
# turns the stub into a working transport, so a test can assert on a push that succeeded
# rather than on one that died in the connection.
STUB_SSH = """#!/bin/sh
printf '%s\\n' "$*" >> "$FD_SSH_LOG"
for arg in "$@"; do cmd="$arg"; done
exec sh -c "$cmd"
"""


def _json(observed_on: str | None, marker: str = "a") -> str:
    return json.dumps({"schema_version": 1, "observed_on": observed_on, "marker": marker})


@dataclass(frozen=True)
class Push:
    returncode: int
    output: str


@dataclass
class Sandbox:
    """A throwaway checkout of the deploy script with its own remote."""

    work: Path
    bare: Path
    key: Path
    ssh_log: Path

    def run(self, **overrides: str) -> Push:
        env = dict(os.environ, FD_DEPLOY_KEY=str(self.key), FD_SSH_LOG=str(self.ssh_log))
        env["PATH"] = f"{self.work.parent / 'bin'}{os.pathsep}{env['PATH']}"
        env.update(overrides)
        result = subprocess.run(
            ["bash", str(self.work / "deploy" / "push-data.sh")],
            cwd=self.work,
            env=env,
            capture_output=True,
            text=True,
        )
        return Push(result.returncode, result.stdout + result.stderr)

    def git(self, *args: str, repo: Path | None = None) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo or self.work,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def write_export(self, observed_on: str | None = "2026-09-09", marker: str = "a") -> None:
        self.work.joinpath(DATA).write_text(_json(observed_on, marker))

    @property
    def subjects(self) -> list[str]:
        """Commit subjects on the remote's main, newest first."""
        return self.git("log", "--format=%s", "main", repo=self.bare).splitlines()

    @property
    def ssh_calls(self) -> list[str]:
        return self.ssh_log.read_text().splitlines() if self.ssh_log.exists() else []


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh = bin_dir / "ssh"
    ssh.write_text(STUB_SSH)
    ssh.chmod(0o755)

    bare = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True
    )

    work = tmp_path / "repo"
    (work / "deploy").mkdir(parents=True)
    (work / "data" / "site").mkdir(parents=True)
    script = work / "deploy" / "push-data.sh"
    script.write_bytes((DEPLOY / "push-data.sh").read_bytes())
    script.chmod(0o755)
    (work / "README.md").write_text("seed\n")
    # The real .gitignore, because it is what makes the export stageable at all: `data/*`
    # excludes the directory and three negations carve `data/site/dashboard.json` back out
    # of it. Simplify that chain and `git add -- <path>` starts refusing with "paths are
    # ignored", which under `set -e` is a silent nightly publish that stops happening.
    (work / ".gitignore").write_bytes(REPO.joinpath(".gitignore").read_bytes())

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=work, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.name", "Seed")
    git("config", "user.email", "seed@example.invalid")
    git("add", "-A")
    git("commit", "-m", "seed")
    # The seed goes in over a plain path so the ssh log holds only the script's own calls.
    git("push", "-q", str(bare), "main")
    # An ssh:// URL is what puts GIT_SSH_COMMAND on the path git actually takes; the stub
    # ssh runs the remote command against this same on-disk bare repo.
    git("remote", "add", "origin", f"ssh://deploy@github.invalid{bare}")

    key = tmp_path / "flight-detective-deploy"
    key.write_text("not a real key\n")
    key.chmod(0o600)
    return Sandbox(work=work, bare=bare, key=key, ssh_log=tmp_path / "ssh.log")


# --- Acceptance: twice with the same JSON commits once ---------------------------------


def test_a_new_export_is_committed_and_pushed(sandbox: Sandbox) -> None:
    sandbox.write_export()
    push = sandbox.run()
    assert push.returncode == 0, push.output
    assert sandbox.subjects == ["data: 2026-09-09", "seed"]


def test_running_twice_with_the_same_json_commits_once(sandbox: Sandbox) -> None:
    sandbox.write_export()
    assert sandbox.run().returncode == 0
    head = sandbox.git("rev-parse", "main", repo=sandbox.bare)

    second = sandbox.run()
    assert second.returncode == 0, second.output
    assert "unchanged" in second.output
    assert sandbox.subjects == ["data: 2026-09-09", "seed"]
    assert sandbox.git("rev-parse", "main", repo=sandbox.bare) == head
    # No commit means no push means no Actions run: the second call must not even connect.
    assert len(sandbox.ssh_calls) == 1


def test_a_changed_export_is_committed_again(sandbox: Sandbox) -> None:
    sandbox.write_export(observed_on="2026-09-09")
    sandbox.run()
    sandbox.write_export(observed_on="2026-09-10", marker="b")
    assert sandbox.run().returncode == 0
    assert sandbox.subjects == ["data: 2026-09-10", "data: 2026-09-09", "seed"]


def test_the_commit_is_authored_by_the_bot_not_the_human(sandbox: Sandbox) -> None:
    sandbox.write_export()
    sandbox.run()
    assert sandbox.git("log", "-1", "--format=%an <%ae>", "main", repo=sandbox.bare) == (
        "flight-detective bot <flight-detective@users.noreply.github.com>"
    )
    # -c on the invocation, not a config write: the human's identity in this checkout stands.
    assert sandbox.git("config", "user.name") == "Seed"


def test_an_export_with_no_observations_still_gets_a_readable_message(
    sandbox: Sandbox,
) -> None:
    # `observed_on` is null until the first ingest lands, and the export runs anyway.
    sandbox.write_export(observed_on=None)
    assert sandbox.run().returncode == 0
    assert sandbox.subjects[0] == "data: no observations"


# --- Acceptance: another modified file is not swept into the commit --------------------


def test_it_commits_only_the_json_when_another_file_is_modified(sandbox: Sandbox) -> None:
    sandbox.write_export()
    sandbox.work.joinpath("README.md").write_text("edited by a human, mid-thought\n")
    assert sandbox.run().returncode == 0

    assert sandbox.git("show", "--name-only", "--format=", "main", repo=sandbox.bare) == DATA
    # And the edit is still sitting in the tree, unstaged, exactly as it was found.
    assert sandbox.git("diff", "--name-only") == "README.md"
    assert sandbox.git("diff", "--cached", "--name-only") == ""


def test_it_refuses_to_run_with_another_file_staged(sandbox: Sandbox) -> None:
    sandbox.write_export()
    sandbox.work.joinpath("README.md").write_text("staged for a commit somebody meant\n")
    sandbox.git("add", "README.md")

    push = sandbox.run()
    assert push.returncode != 0
    assert "refusing to run" in push.output and "README.md" in push.output
    assert sandbox.subjects == ["seed"]
    assert sandbox.git("diff", "--cached", "--name-only") == "README.md"


def test_an_already_staged_export_is_not_mistaken_for_someone_elses_work(
    sandbox: Sandbox,
) -> None:
    sandbox.write_export()
    sandbox.git("add", DATA)
    assert sandbox.run().returncode == 0
    assert sandbox.subjects[0] == "data: 2026-09-09"


# --- The other refusals: they all happen before anything is committed ------------------


@pytest.mark.parametrize(
    ("break_it", "expected"),
    [
        pytest.param(lambda s: s.work.joinpath(DATA).unlink(), "does not exist", id="no export"),
        pytest.param(lambda s: s.key.unlink(), "no deploy key", id="no deploy key"),
        pytest.param(
            lambda s: s.git("checkout", "-q", "-b", "dev"), "expected 'main'", id="wrong branch"
        ),
    ],
)
def test_a_refusal_leaves_no_commit_behind(
    sandbox: Sandbox, break_it: Callable[[Sandbox], None], expected: str
) -> None:
    sandbox.write_export()
    break_it(sandbox)

    push = sandbox.run()
    assert push.returncode != 0
    assert expected in push.output
    assert sandbox.subjects == ["seed"]
    assert sandbox.git("log", "--format=%s", "-1") == "seed"


def test_the_branch_it_pushes_can_be_overridden(sandbox: Sandbox) -> None:
    # The box's checkout is on main; a test deployment on another branch should not need
    # the script edited. deploy-site.yml still only watches main, so this is a knob for
    # rehearsing the chain, not a second way to publish.
    sandbox.git("checkout", "-q", "-b", "staging")
    sandbox.write_export()
    assert sandbox.run(FD_BRANCH="staging").returncode == 0
    assert sandbox.git("log", "--format=%s", "-1", "staging", repo=sandbox.bare) == (
        "data: 2026-09-09"
    )


# --- Acceptance: the push uses the deploy key and nothing else -------------------------


def test_the_push_offers_the_deploy_key_and_only_the_deploy_key(sandbox: Sandbox) -> None:
    sandbox.write_export()
    assert sandbox.run().returncode == 0

    (call,) = sandbox.ssh_calls
    assert f"-i {sandbox.key}" in call
    # Without IdentitiesOnly, -i is a preference: ssh still offers every key the agent
    # holds, and an unattended push ends up authenticating as whoever ran it.
    assert "-o IdentitiesOnly=yes" in call


# --- The script as run-pipeline.sh calls it --------------------------------------------


def test_the_committed_script_is_executable() -> None:
    # run-pipeline.sh calls it as a path, not as `bash …`, so the bit is load-bearing.
    assert os.access(DEPLOY / "push-data.sh", os.X_OK)


def test_the_export_is_not_git_ignored_in_the_real_repo() -> None:
    # The sandbox copies .gitignore, but only the real tree can prove the negations still
    # resolve for the one path push-data.sh stages.
    ignored = subprocess.run(["git", "check-ignore", "-q", DATA], cwd=REPO, capture_output=True)
    assert ignored.returncode == 1, f"{DATA} is git-ignored: push-data.sh cannot stage it"


UNITS = ("flight-detective-pipeline.service", "flight-detective-pipeline.timer")


def _install(tmp_path: Path, branch: str | None = "main", **overrides: str) -> str:
    """Run the real `deploy/install.sh` out of a throwaway checkout of `deploy/` whose HEAD
    the test controls, and return everything it said.

    Not run against this repo: the branch warnings would then depend on how the code was
    checked out, and `actions/checkout` leaves a detached HEAD for `pull_request` events —
    which is `branch=None` here. `systemctl` is stubbed out, since enabling a timer is the
    one thing the installer does outside the directory it is given."""
    repo = tmp_path / f"checkout-{branch or 'detached'}"
    (repo / "deploy").mkdir(parents=True)
    for name in ("install.sh", "run-pipeline.sh", *UNITS):
        copy = repo / "deploy" / name
        copy.write_bytes(DEPLOY.joinpath(name).read_bytes())
        copy.chmod(0o755)  # the units are data, but ExecStart's target must be runnable

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)

    git("init", "-b", branch or "main")
    git("config", "user.name", "Seed")
    git("config", "user.email", "seed@example.invalid")
    git("add", "-A")
    git("commit", "-m", "seed")
    if branch is None:
        git("checkout", "-q", "--detach")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    stub = fake_bin / "systemctl"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "config"), **overrides)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        ["bash", str(repo / "deploy" / "install.sh")], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def test_the_installer_prints_the_deploy_key_steps_it_cannot_take(tmp_path: Path) -> None:
    # Registering a key and switching Pages on are GitHub-UI actions; the installer's job
    # is to say so, in the one place someone setting this box up is already looking.
    output = _install(tmp_path, FD_DEPLOY_KEY=str(tmp_path / "absent"))
    assert "ssh-keygen" in output and str(tmp_path / "absent") in output
    assert "Allow write access" in output
    assert 'Source: "GitHub Actions"' in output
    # S09's warning retires here: the chain now has its last step.
    assert "push-data.sh does not exist yet" not in output


def test_the_installer_is_quiet_about_a_deploy_key_that_is_there(tmp_path: Path) -> None:
    key = tmp_path / "present"
    key.write_text("not a real key\n")
    assert "ssh-keygen" not in _install(tmp_path, FD_DEPLOY_KEY=str(key))


def test_the_installer_warns_when_the_checkout_is_on_the_wrong_branch(tmp_path: Path) -> None:
    # push-data.sh refuses from any branch but the one it pushes, and a box set up
    # mid-development sits on a feature branch. Better said at install time than at 06:30.
    output = _install(tmp_path, branch="dev")
    assert "this checkout is on 'dev'" in output
    assert "only publishes" in output and "'main'" in output


def test_the_installer_is_quiet_about_a_checkout_that_can_publish(tmp_path: Path) -> None:
    assert "this checkout is on" not in _install(tmp_path, branch="main")


def test_the_installer_says_something_useful_about_a_detached_head(tmp_path: Path) -> None:
    # No branch at all is not "on ''": an exported tarball or a CI checkout gets here, and
    # the installer must not print what reads as its own bug.
    output = _install(tmp_path, branch=None)
    assert "not a git checkout sitting on a branch" in output
    assert "on ''" not in output


# --- Acceptance: deploy-site.yml rebuilds on the data and on web/, and on nothing else --


def _push_paths() -> list[str]:
    """`on.push.paths` out of deploy-site.yml. Hand-parsed: the project has no YAML
    dependency, and adding one to assert on a six-line list we wrote is a poor trade."""
    lines = (REPO / ".github" / "workflows" / "deploy-site.yml").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "paths:")
    paths = []
    for line in lines[start + 1 :]:
        if not line.strip().startswith("- "):
            break
        paths.append(line.strip().removeprefix("- ").strip('"'))
    return paths


def _rebuilds(changed: str) -> bool:
    """GitHub's `paths:` matching, near enough for our four globs: `**` spans directories."""
    return any(fnmatch(changed, pattern.replace("**", "*")) for pattern in _push_paths())


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        (DATA, True),
        ("web/src/App.tsx", True),
        ("web/index.html", True),
        (".github/workflows/deploy-site.yml", True),
        ("src/flight_detective/ingest.py", False),
        ("deploy/push-data.sh", False),
        ("specs/backlog.md", False),
        ("README.md", False),
        # The export is the only file under data/ the site is built from; the rest of the
        # directory is git-ignored anyway, but a stray commit there must not rebuild.
        ("data/site/other.json", False),
    ],
)
def test_only_the_site_and_its_data_trigger_a_rebuild(changed: str, expected: bool) -> None:
    assert _rebuilds(changed) is expected


def test_the_deploy_builds_under_the_repos_own_pages_path() -> None:
    # A project site is served from /<repo>/, and the bundle's asset URLs are baked in at
    # build time, so a base that does not match the repo name deploys a page whose every
    # asset 404s. Taking it from the event keeps that true through a rename.
    workflow = (REPO / ".github" / "workflows" / "deploy-site.yml").read_text()
    assert "VITE_BASE: /${{ github.event.repository.name }}/" in workflow
