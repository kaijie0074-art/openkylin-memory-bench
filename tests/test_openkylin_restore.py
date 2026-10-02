"""Synthetic files/processes only. Never operates on a VM or installs a package."""
import base64
import hashlib
import http.client
import importlib.util
import io
import json
import os
import struct
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), SCRIPTS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


restore = load("openkylin-restore")
bootstrap = load("openkylin-bootstrap")
data = load("prepare-openkylin-data")
UUID = "11111111-2222-3333-4444-555555555555"


class FakeGuest:
    def __init__(self, root):
        self.root = root
        self.calls = []
        self.mounted = True
        self.disk_uuid = UUID
        self.filesystem = "ext4"
        self.active = False
        self.containers = ""
        self.processes = "1 /sbin/init\n"
        self.mount_source = "/dev/vda"
        self.mount_children = []
        self.packages = restore.PACKAGES.copy()
        self.accounts = True
        self.override = {}
        for name, text in {"/etc/os-release": 'ID=openkylin\nVERSION_ID="3.0"\n',
                           "/proc/cmdline": "boot=casper quiet"}.items():
            path = root / name.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (root / "mnt/kmb").mkdir(parents=True)

    def __call__(self, args, **kwargs):
        assert kwargs["env"] == restore.CLEAN_ENV
        self.calls.append(args)
        code, out = 0, ""
        if tuple(args) in self.override:
            code, out = self.override[tuple(args)]
        elif args == ["uname", "-m"]:
            out = "aarch64\n"
        elif args[0] == "lsblk":
            out = json.dumps({"blockdevices": [{"name": "vda", "type": "disk",
                "size": restore.SIZE, "ro": False, "fstype": self.filesystem,
                "mountpoints": [str(self.root / "mnt/kmb")] if self.mounted else []}]})
        elif args[:3] == ["blkid", "-s", "LABEL"]:
            out = "KMB_DATA\n"
        elif args[:3] == ["blkid", "-s", "UUID"]:
            out = self.disk_uuid + "\n"
        elif args[:2] == ["findmnt", "-n"]:
            code, out = (0, self.mount_source + "\n") if self.mounted else (1, "")
        elif args[:2] == ["findmnt", "-J"]:
            out = json.dumps({"filesystems": [{"target": str(self.root / "mnt/kmb"),
                                               "source": self.mount_source, "fstype": "ext4",
                                               "children": self.mount_children}]})
        elif args[0] == "getent":
            if not self.accounts:
                code = 2
            elif args[1] == "passwd":
                out = "kmb:x:10001:10001::/mnt/kmb/home:/bin/bash\n"
            else:
                out = "kmb:x:10001:\n"
        elif args[0] == "dpkg-query":
            version = self.packages.get(args[-1])
            code, out = (0, "install ok installed\t" + version) if version else (1, "")
        elif args[:2] == ["systemctl", "is-active"]:
            code = 0 if self.active else 3
        elif args[0].endswith("/docker") and "ps" in args:
            out = self.containers
        elif args[0].endswith("/docker") and "info" in args:
            out = json.dumps({"DockerRootDir": "/mnt/kmb/docker"})
        elif args[0] == "ps":
            out = self.processes
        elif args == ["id", "-nG", "kmb"]:
            out = "kmb docker\n"
        elif args[0] == "mount":
            self.mounted = True
        elif args == ["systemctl", "start", "containerd", "docker"]:
            self.active = True
        else:
            raise AssertionError("Unexpected command: " + repr(args))
        return subprocess.CompletedProcess(args, code, out, "")


@pytest.fixture
def guest(tmp_path):
    fake = FakeGuest(tmp_path)
    system = restore.System(tmp_path, fake, trusted_uid=os.getuid())
    return system, fake


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_default_inspection_and_non_execute_stage_never_mutate(guest, monkeypatch):
    system, fake = guest
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-pass")
    monkeypatch.setenv("KMB_MODEL_API_KEY", "must-not-pass-either")
    before = files(system.root)
    for stage in restore.STAGES:
        result = restore.restore(system, stage, UUID)
        assert result["read_only"] and result["execute"] is False
    assert files(system.root) == before
    assert all(call[0] in {"uname", "lsblk", "blkid", "findmnt", "getent", "dpkg-query",
                           "systemctl", "ps"} for call in fake.calls)


@pytest.mark.parametrize("change", ["uuid", "blank", "foreign_mount", "os", "architecture"])
def test_wrong_identity_fails_before_writes(guest, change):
    system, fake = guest
    if change == "uuid":
        fake.disk_uuid = "00000000-2222-3333-4444-555555555555"
    elif change == "blank":
        fake.filesystem = None
    elif change == "foreign_mount":
        fake.mount_source = "/dev/other"
    elif change == "os":
        system.path("/etc/os-release").write_text("ID=debian\nVERSION_ID=3.0\n")
    else:
        fake.override[("uname", "-m")] = (0, "x86_64\n")
    before = files(system.root)
    with pytest.raises(RuntimeError):
        restore.restore(system, "mount", UUID, execute=True)
    assert files(system.root) == before
    assert not any(c[0] in {"mount", "mkfs.ext4", "usermod"} for c in fake.calls)


def test_uuid_cannot_be_omitted_or_shortened(guest):
    system, _ = guest
    for bad in ("", "KMB_DATA", "11111111", "../disk"):
        with pytest.raises(RuntimeError, match="UUID"):
            restore.inspect(system, bad)


@pytest.mark.parametrize("busy", ["container", "runner", "worker"])
def test_busy_guest_refuses_every_mutating_stage(guest, busy):
    system, fake = guest
    if busy == "container":
        fake.active, fake.containers = True, "synthetic-container-id\n"
    elif busy == "runner":
        fake.processes += "20 /mnt/kmb/project/.venv/bin/kmb inspect run suite\n"
    else:
        fake.processes += "21 python3 /tmp/agent-worker.py\n"
    before = files(system.root)
    for stage in restore.STAGES[1:]:
        with pytest.raises(RuntimeError, match="active containers/runner"):
            restore.restore(system, stage, UUID, execute=True)
    assert files(system.root) == before


@pytest.mark.parametrize("still_running", [True, False])
def test_docker_overlay_is_busy_not_a_disk_error_and_blocks_all_mutation(guest, still_running):
    system, fake = guest
    fake.active = still_running
    fake.containers = "synthetic-container-id\n" if still_running else ""
    fake.mount_children = [{
        "target": str(system.path("/mnt/kmb/docker/overlay2/" + "a" * 64 + "/merged")),
        "source": "overlay", "fstype": "overlay",
    }]
    before = files(system.root)
    state = restore.restore(system, "inspect", UUID)
    assert state["read_only"] and state["restore_blocked"]
    assert state["docker_overlay_mounts"] == 1
    assert state["running_containers"] is still_running
    for stage in restore.STAGES[1:]:
        with pytest.raises(RuntimeError, match="Docker overlay mounts"):
            restore.restore(system, stage, UUID, execute=True)
    assert files(system.root) == before
    assert all(c[0] not in {"mount", "mkfs.ext4", "usermod", "groupadd", "useradd"}
               and c[:2] != ["systemctl", "stop"] for c in fake.calls)


@pytest.mark.parametrize("bad", ["outside_docker", "wrong_type", "wrong_source", "nested"])
def test_unknown_nested_mounts_still_fail_closed(guest, bad):
    system, fake = guest
    child = {
        "target": str(system.path("/mnt/kmb/docker/overlay2/" + "a" * 64 + "/merged")),
        "source": "overlay", "fstype": "overlay",
    }
    if bad == "outside_docker":
        child["target"] = str(system.path("/mnt/kmb/project/merged"))
    elif bad == "wrong_type":
        child["fstype"] = "ext4"
    elif bad == "wrong_source":
        child["source"] = "/dev/other"
    else:
        child["children"] = [{"target": child["target"] + "/unknown"}]
    fake.mount_children = [child]
    before = files(system.root)
    with pytest.raises(RuntimeError, match="unexpected/nested"):
        restore.inspect(system, UUID)
    assert files(system.root) == before


def test_all_config_conflicts_checked_before_any_write(guest):
    system, _ = guest
    path = system.path("/etc/containerd/config.toml")
    path.parent.mkdir(parents=True)
    path.write_text("unknown original data\n")
    before = files(system.root)
    with pytest.raises(RuntimeError, match="configuration"):
        restore.managed_configs(system)
    assert files(system.root) == before


def test_known_configs_are_idempotent_and_semantic_json_is_accepted(guest):
    system, _ = guest
    restore.managed_configs(system)
    daemon = system.path("/etc/docker/daemon.json")
    daemon.write_text(json.dumps({"data-root": "/mnt/kmb/docker"}, indent=2))
    before = files(system.root)
    restore.managed_configs(system)
    assert files(system.root) == before


def test_managed_config_parent_symlink_rejected(guest, tmp_path):
    system, _ = guest
    external = tmp_path / "external"
    external.mkdir()
    system.path("/etc/docker").symlink_to(external, target_is_directory=True)
    with pytest.raises(RuntimeError, match="symlink"):
        restore.managed_configs(system)
    assert list(external.iterdir()) == []


def test_active_docker_missing_config_is_not_stopped_or_reconfigured(guest):
    system, fake = guest
    fake.active = True
    before = files(system.root)
    with pytest.raises(RuntimeError, match="active Docker"):
        restore.restore(system, "docker", UUID, execute=True)
    assert files(system.root) == before
    assert not any(c[:2] == ["systemctl", "stop"] for c in fake.calls)


def test_matching_active_docker_is_not_restarted_or_reinstalled(guest):
    system, fake = guest
    restore.managed_configs(system)
    for name in ("docker", "containerd"):
        system.path("/mnt/kmb/" + name).mkdir()
    fake.active = True
    restore.restore(system, "docker", UUID, execute=True)
    assert not any(c[0] == "apt-get" or c[:2] in (["systemctl", "stop"],
                   ["systemctl", "start"], ["systemctl", "daemon-reload"]) for c in fake.calls)


def test_installed_package_drift_is_not_automatically_corrected(guest):
    system, fake = guest
    fake.packages["docker.io"] = "different"
    with pytest.raises(RuntimeError, match="no automatic upgrade/downgrade"):
        restore.restore(system, "packages", UUID, execute=True)
    assert not any(c[0] == "apt-get" for c in fake.calls)


def test_mount_does_not_initialize_and_repeated_mount_preserves_history(guest):
    system, fake = guest
    fake.mounted = False
    assert restore.restore(system, "mount", UUID, execute=True)["stage"] == "mount"
    history = system.path("/mnt/kmb/.restore-events")
    initial = files(history)
    restore.restore(system, "mount", UUID, execute=True)
    assert len(list(history.glob("*.json"))) == 2
    assert all((history / key).read_bytes() == value for key, value in initial.items())
    assert sum(c[0] == "mount" for c in fake.calls) == 1
    assert not any("mkfs" in c[0] for c in fake.calls)


def test_nonempty_unmounted_directory_is_never_hidden(guest):
    system, fake = guest
    fake.mounted = False
    system.path("/mnt/kmb/do-not-hide").write_text("original")
    with pytest.raises(RuntimeError, match="hide"):
        restore.restore(system, "mount", UUID, execute=True)


def archive(tmp_path, members):
    path = tmp_path / "project.tar.gz"
    with tarfile.open(path, "w:gz") as bundle:
        for name, kind in members:
            entry = tarfile.TarInfo(name)
            if kind == "symlink":
                entry.type, entry.linkname = tarfile.SYMTYPE, "/outside"
                bundle.addfile(entry)
            elif kind == "hardlink":
                entry.type, entry.linkname = tarfile.LNKTYPE, "/outside"
                bundle.addfile(entry)
            else:
                content = b"synthetic\n"
                entry.size = len(content)
                bundle.addfile(entry, io.BytesIO(content))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("bad,kind", [("../escape", "file"), ("/absolute", "file"),
    ("nested/link", "symlink"), ("nested/hardlink", "hardlink"), (".env.local", "file"),
    ("reports/old.json", "file"), ("pyproject.toml", "file")])
def test_unsafe_archive_is_rejected_before_destination_changes(tmp_path, bad, kind):
    bundle, digest = archive(tmp_path, [("pyproject.toml", "file"), ("uv.lock", "file"),
                                      (bad, kind)])
    destination = tmp_path / "project"
    destination.mkdir()
    with pytest.raises(RuntimeError, match="unsafe"):
        restore.extract_new_project(bundle, destination, digest)
    assert list(destination.iterdir()) == []
    assert not list(tmp_path.glob(".project-stage-*"))


def test_project_archive_hash_and_existing_project_are_protected(tmp_path):
    bundle, digest = archive(tmp_path, [("pyproject.toml", "file"), ("uv.lock", "file")])
    destination = tmp_path / "project"
    with pytest.raises(RuntimeError, match="SHA256"):
        restore.extract_new_project(bundle, destination, "0" * 64)
    assert not destination.exists()
    restore.extract_new_project(bundle, destination, digest)
    before = files(destination)
    with pytest.raises(RuntimeError, match="not empty"):
        restore.extract_new_project(bundle, destination, digest)
    assert files(destination) == before


def test_initial_record_is_immutable_and_events_append(tmp_path):
    record = {"device": "/dev/vda", "bytes": data.SIZE, "label": "KMB_DATA",
              "filesystem_uuid": UUID, "uid": 10001, "gid": 10001, "initialized_now": True}
    data.preserve_initialization(tmp_path, record)
    original = (tmp_path / "data-initialization.json").read_bytes()
    data.preserve_initialization(tmp_path, {**record, "initialized_now": False})
    assert (tmp_path / "data-initialization.json").read_bytes() == original
    assert len(list((tmp_path / ".data-events").glob("*.json"))) == 2
    with pytest.raises(RuntimeError, match="different"):
        data.preserve_initialization(tmp_path, {**record, "filesystem_uuid": "other"})
    assert (tmp_path / "data-initialization.json").read_bytes() == original


def test_prepare_blank_has_explicit_gate_before_mkfs(tmp_path, monkeypatch):
    fake = FakeGuest(tmp_path)
    fake.filesystem, fake.mounted, fake.accounts = None, False, False
    monkeypatch.setattr(data.os, "geteuid", lambda: 0)
    original = data.Path
    monkeypatch.setattr(data, "ROOT", tmp_path / "mnt/kmb")
    monkeypatch.setattr(data, "Path", lambda name: original(tmp_path / name.lstrip("/")))
    monkeypatch.setattr(data, "run", lambda *a, required=True: fake(list(a), env=restore.CLEAN_ENV))
    with pytest.raises(RuntimeError, match="--initialize-blank"):
        data.main()
    assert not any(c[0] == "mkfs.ext4" for c in fake.calls)


def test_synthetic_public_key_validated_and_comment_removed(tmp_path):
    raw = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(range(32))
    key = "ssh-ed25519 " + base64.b64encode(raw).decode()
    path = tmp_path / "synthetic.pub"
    path.write_text(key + " ignored-comment;not-code\n")
    assert bootstrap.public_key(path) == key
    body = bootstrap.render(bootstrap.public_key(path))
    assert b"ignored-comment" not in body and b"PRIVATE KEY" not in body
    assert b"PermitRootLogin no" in body and b"PasswordAuthentication no" in body
    assert b"openssh-server=1:10.2p1-ok3" in body
    assert subprocess.run(["sh", "-n"], input=body, capture_output=True, check=False).returncode == 0


@pytest.mark.parametrize("name,contents", [("private", "private bytes"),
    ("fake.pub", "-----BEGIN OPENSSH PRIVATE KEY-----"),
    ("fake.pub", "ssh-ed25519 not-base64"),
    ("fake.pub", "ssh-ed25519 YWJj\nssh-ed25519 YWJj")])
def test_private_or_malformed_key_input_is_rejected(tmp_path, name, contents):
    path = tmp_path / name
    path.write_text(contents)
    with pytest.raises(ValueError):
        bootstrap.public_key(path)


def test_http_server_serves_only_synthetic_body_on_loopback():
    with bootstrap.make_server(b"synthetic public bootstrap", route="/abc123") as server:
        assert server.server_address[0] == "127.0.0.1"
        for method, path, expected in [("GET", "/abc123", 200), ("GET", "/", 404),
                                       ("GET", "/../private", 404), ("POST", "/abc123", 501)]:
            thread = threading.Thread(target=server.handle_request)
            thread.start()
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            client.request(method, path)
            response = client.getresponse()
            assert response.status == expected
            content = response.read()
            if expected == 200:
                assert content == b"synthetic public bootstrap"
            client.close()
            thread.join(timeout=2)
            assert not thread.is_alive()


def test_package_preview_distinguishes_new_install_arch_from_upgrade():
    restore.validate_package_preview("Inst docker.io (24.0.7-ok3 openKylin [arm64])\n")
    for bad in ("Inst docker.io [24.0.6] (24.0.7-ok3 openKylin [arm64])", "Remv old-package [1.0]"):
        with pytest.raises(RuntimeError, match="removals/upgrades"):
            restore.validate_package_preview(bad)


@pytest.mark.parametrize("value", ["http://user:secret@127.0.0.1:17897", "http://remote.test:17897",
    "http://127.0.0.1:bad-secret", "http://127.0.0.1:17897/private", "https://127.0.0.1:17897"])
def test_package_proxy_never_imports_credentials_or_arbitrary_destination(value):
    with pytest.raises(RuntimeError) as caught:
        restore.package_proxy(value)
    assert "secret" not in str(caught.value)
    assert restore.package_proxy(None) == []
    assert restore.package_proxy("http://127.0.0.1:17897") == [
        "HTTP_PROXY=http://127.0.0.1:17897", "HTTPS_PROXY=http://127.0.0.1:17897"]


def test_uv_archive_does_not_execute_extract_other_members_or_replace_existing(tmp_path):
    bundle, digest = archive(tmp_path, [("uv-aarch64-unknown-linux-gnu/uv", "file"),
                                       ("../ignored", "file")])
    destination = tmp_path / "uv"
    restore.install_uv(bundle, destination, digest)
    assert destination.read_bytes() == b"synthetic\n"
    assert not (tmp_path.parent / "ignored").exists()
    restore.install_uv(bundle, destination, digest)
    destination.write_bytes(b"existing differs")
    with pytest.raises(RuntimeError, match="no automatic replacement"):
        restore.install_uv(bundle, destination, digest)
    assert destination.read_bytes() == b"existing differs"


def test_dependencies_are_offline_frozen_and_model_environment_is_absent(guest, tmp_path, monkeypatch):
    system, fake = guest
    project = system.path("/mnt/kmb/project")
    project.mkdir()
    (project / "pyproject.toml").write_text("synthetic")
    (project / "uv.lock").write_text("locked")
    system.path("/mnt/kmb/bin").mkdir()
    bundle, digest = archive(tmp_path, [("uv-aarch64-unknown-linux-gnu/uv", "file")])
    monkeypatch.setattr(restore.os, "chown", lambda *a: None)
    runner = ("runuser", "-u", "kmb", "--", "env", "-i", "HOME=/mnt/kmb/home",
              "PATH=/mnt/kmb/bin:/opt/system/bin:/usr/bin:/bin", "UV_CACHE_DIR=/mnt/kmb/cache/uv",
              "UV_PYTHON_DOWNLOADS=never", str(system.path("/mnt/kmb/bin/uv")))
    fake.override[(*runner, "--version")] = (0, "uv 0.11.15\n")
    sync = (*runner, "sync", "--project", "/mnt/kmb/project", "--frozen", "--no-dev",
            "--python", "/usr/bin/python3", "--offline")
    fake.override[sync] = (0, "")
    restore.restore(system, "dependencies", UUID, execute=True, uv_archive=bundle, uv_sha256=digest)
    assert list(sync) in fake.calls
    assert (project / "uv.lock").read_text() == "locked"


def policy_snippets(tmp_path):
    body = bootstrap.render('ssh-ed25519 SYNTHETIC').decode()
    create = body.split("<<'CREATEPOLICY'\n", 1)[1].split("\nCREATEPOLICY", 1)[0]
    cleanup = body.split("<<'CLEANPOLICY'\n", 1)[1].split("\nCLEANPOLICY", 1)[0]
    target = tmp_path / "policy-rc.d"
    return target, [s.replace("'/usr/sbin/policy-rc.d'", repr(str(target)))
                    for s in (create, cleanup)]


def test_bootstrap_policy_dangling_symlink_never_creates_target(tmp_path):
    target, (create, _) = policy_snippets(tmp_path)
    other = tmp_path / "must-not-create"
    target.symlink_to(other)
    result = subprocess.run([os.sys.executable, "-c", create], capture_output=True, check=False)
    assert result.returncode != 0 and target.is_symlink() and not other.exists()


def test_bootstrap_policy_cleanup_preserves_replaced_file(tmp_path):
    target, (create, cleanup) = policy_snippets(tmp_path)
    result = subprocess.run([os.sys.executable, "-c", create], capture_output=True,
                            text=True, check=True)
    replacement = tmp_path / "replacement"
    replacement.write_text("someone else's policy\n")
    replacement.replace(target)
    cleaned = subprocess.run([os.sys.executable, "-c", cleanup, result.stdout.strip()],
                             capture_output=True, check=False)
    assert cleaned.returncode != 0
    assert target.read_text() == "someone else's policy\n"


def test_bootstrap_policy_cleanup_only_removes_own_inode(tmp_path):
    target, (create, cleanup) = policy_snippets(tmp_path)
    result = subprocess.run([os.sys.executable, "-c", create], capture_output=True,
                            text=True, check=True)
    subprocess.run([os.sys.executable, "-c", cleanup, result.stdout.strip()],
                   capture_output=True, check=True)
    assert not target.exists()


def test_docker_root_accepts_only_verified_mnt_alias(tmp_path):
    actual = tmp_path / "var/mnt/kmb/docker"
    actual.mkdir(parents=True)
    (tmp_path / "mnt").symlink_to(tmp_path / "var/mnt", target_is_directory=True)
    system = restore.System(tmp_path, trusted_uid=os.getuid())
    assert restore.docker_root_matches(system, "/mnt/kmb/docker")
    assert restore.docker_root_matches(system, "/var/mnt/kmb/docker")
    (tmp_path / "arbitrary-alias").symlink_to(actual)
    for bad in ("/var/lib/docker", "/arbitrary-alias", "/var/mnt/kmb/../docker", None):
        assert not restore.docker_root_matches(system, bad)


def test_bootstrap_limits_all_effective_public_key_authorization_sources():
    body = bootstrap.render('ssh-ed25519 SYNTHETIC').decode()
    for value in ("authorizedkeysfile /home/openkylin/.ssh/authorized_keys",
                  "authorizedkeyscommand none", "trustedusercakeys none",
                  "authorizedprincipalsfile none", "authorizedprincipalscommand none",
                  "pubkeyacceptedalgorithms ssh-ed25519", "authenticationmethods publickey",
                  "allowagentforwarding no", "x11forwarding no"):
        assert "'" + value + "'" in body
    assert body.index('/usr/sbin/sshd -T') < body.index('install -m 600 -o openkylin')
    assert "if systemctl is-active --quiet ssh || systemctl is-active --quiet ssh.socket" in body


@pytest.mark.parametrize("command", [
    "python3 /mnt/kmb/project/scripts/probe_subject_isolation.py --agent both",
    "python3 /mnt/kmb/project/scripts/calibrate-scorers.py --with-model",
    "python3 /mnt/kmb/project/src/kmb/cli.py run --agent both",
    "python3 -m kmb.cli run --agent both",
])
def test_known_python_entrypoints_block_restore_between_container_calls(guest, command):
    system, fake = guest
    fake.processes += "400 " + command + "\n"
    assert restore.inspect(system, UUID)["runner_process_present"]
    with pytest.raises(RuntimeError, match="active containers/runner"):
        restore.restore(system, "mount", UUID, execute=True)


def test_existing_prepare_path_is_read_only_even_without_runner_account(tmp_path, monkeypatch, capsys):
    fake = FakeGuest(tmp_path)
    fake.mounted, fake.accounts = False, False
    monkeypatch.setattr(data.os, "geteuid", lambda: 0)
    original = data.Path
    monkeypatch.setattr(data, "ROOT", tmp_path / "mnt/kmb")
    monkeypatch.setattr(data, "Path", lambda name: original(tmp_path / name.lstrip("/")))
    monkeypatch.setattr(data, "run", lambda *a, required=True: fake(list(a), env=restore.CLEAN_ENV))
    monkeypatch.setattr(data.os, "chown", lambda *a: pytest.fail("chown must not run"))
    before = files(tmp_path)
    data.main(expected_uuid=UUID)
    assert json.loads(capsys.readouterr().out)["read_only"]
    assert files(tmp_path) == before
    assert all(c[0] not in {"mount", "mkfs.ext4", "groupadd", "useradd"} for c in fake.calls)


@pytest.mark.parametrize("extra,success", [
    ("# Match User example\n", True),
    ("Match User openkylin\n AuthorizedKeysFile /other\n", False),
    ("Match Address 192.168.5.2\n AuthenticationMethods password\n", False),
    ("Match=User openkylin\n AuthorizedKeysFile /other\n", False),
    ("Match = User openkylin\n AuthorizedKeysFile /other\n", False),
    ("Match =User openkylin\n AuthorizedKeysFile /other\n", False),
    ("Include /private/unrelated.conf\n", False),
    ("Include=/private/unrelated.conf\n", False),
    ("Include = /private/unrelated.conf\n", False),
    ("Include =/private/unrelated.conf\n", False),
    ("# Match=User example\n# Include=/private/ignored.conf\n", True),
])
def test_bootstrap_refuses_conditional_or_external_ssh_config(tmp_path, extra, success):
    base = tmp_path / "ssh"
    (base / "sshd_config.d").mkdir(parents=True)
    (base / "sshd_config").write_text('Include ' + str(base / 'sshd_config.d/*.conf') + '\n')
    (base / "sshd_config.d/existing.conf").write_text(extra)
    body = bootstrap.render('ssh-ed25519 SYNTHETIC').decode()
    checker = body.split("<<'CHECKMATCH'\n", 1)[1].split("\nCHECKMATCH", 1)[0]
    checker = checker.replace("Path('/etc/ssh')", "Path(" + repr(str(base)) + ")")
    result = subprocess.run([os.sys.executable, "-c", checker], capture_output=True, check=False)
    assert (result.returncode == 0) is success
