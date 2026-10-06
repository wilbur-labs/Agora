"""Reading ~/.ssh/config to pre-fill a registration (CR-25)."""
from __future__ import annotations

from pathlib import Path

from agora.ssh_config import read_ssh_config


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_hosts_with_their_values_first_value_wins(tmp_path):
    config = _write(tmp_path / ".ssh" / "config", """
# 社内サーバー（コメントは UTF-8）
Host AI-Server
    HostName 10.36.94.14
    User sharp

Host vps
    HostName vps.example.test
    Port 2222
    IdentityFile ~/.ssh/vps_ed25519

Host *
    User fallback
    Port 22
""")
    read = read_ssh_config(config)
    assert read.status == "ok"
    by = {h.alias: h for h in read.hosts}
    assert set(by) == {"AI-Server", "vps"}  # the wildcard block is defaults, not a host
    assert (by["AI-Server"].hostname, by["AI-Server"].user, by["AI-Server"].port) == ("10.36.94.14", "sharp", 22)
    assert (by["vps"].user, by["vps"].port, by["vps"].identity_file) == ("fallback", 2222, "~/.ssh/vps_ed25519")


def test_negation_multiple_patterns_equals_syntax_and_include(tmp_path):
    ssh = tmp_path / ".ssh"
    _write(ssh / "conf.d" / "work.conf", "Host work-a work-b\n  HostName=10.0.0.9\n")
    config = _write(ssh / "config", """
Include conf.d/*.conf
Host * !work-b
    User everyone
""")
    by = {h.alias: h for h in read_ssh_config(config).hosts}
    assert by["work-a"].hostname == "10.0.0.9" and by["work-a"].user == "everyone"
    assert by["work-b"].hostname == "10.0.0.9" and by["work-b"].user is None


def test_match_blocks_are_skipped(tmp_path):
    config = _write(tmp_path / ".ssh" / "config", """
Host a
    HostName 1.1.1.1
Match host a
    User from-match
""")
    (a,) = read_ssh_config(config).hosts
    assert a.user is None


def test_absent_and_unreadable_are_different_answers(tmp_path):
    assert read_ssh_config(tmp_path / "nope").status == "absent"
    bad = tmp_path / "config"
    bad.write_bytes(b"Host x\n  HostName \xff\xfe\n")
    read = read_ssh_config(bad)
    assert read.status == "unreadable" and read.hosts == []


def test_the_endpoint_reports_status_and_hosts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from agora.api import settings_router
    from agora.api.app import app

    config = _write(tmp_path / ".ssh" / "config", "Host box\n  HostName 10.1.2.3\n  User me\n")
    monkeypatch.setattr(settings_router, "read_ssh_config", lambda: read_ssh_config(config))
    body = TestClient(app).get("/api/ssh-config/hosts").json()
    assert body["status"] == "ok"
    assert body["hosts"] == [{"alias": "box", "hostname": "10.1.2.3", "user": "me", "port": None, "identity_file": None}]


def test_every_spelling_of_the_equals_separator(tmp_path):
    """review: OpenSSH accepts `Key value`, `Key=value` and `Key = value`."""
    config = _write(tmp_path / ".ssh" / "config", (
        "Host a\n  HostName = 10.0.0.1\n  User =me\n  Port= 2200\n"
        "Host b\n  HostName=10.0.0.2\n  IdentityFile = ~/.ssh/b key\n"
    ))
    by = {h.alias: h for h in read_ssh_config(config).hosts}
    assert (by["a"].hostname, by["a"].user, by["a"].port) == ("10.0.0.1", "me", 2200)
    assert by["b"].hostname == "10.0.0.2"


def test_inline_comments_do_not_become_hosts(tmp_path):
    """review: `Host box # production server` names one host."""
    config = _write(tmp_path / ".ssh" / "config", 'Host box # production server\n  HostName 10.0.0.1 # main\nHost "with # hash"\n  HostName 10.0.0.2\n')
    by = {h.alias: h for h in read_ssh_config(config).hosts}
    assert set(by) == {"box", "with # hash"}
    assert by["box"].hostname == "10.0.0.1"


def test_an_include_inside_a_host_block_applies_only_to_that_host(tmp_path):
    """review: OpenSSH evaluates it under the block's condition; flattening it gave
    another host this one's address."""
    ssh = tmp_path / ".ssh"
    _write(ssh / "a.conf", "Host *\n  HostName 10.0.0.1\n")
    config = _write(ssh / "config", "Host a\n  Include a.conf\n  User after-include\nHost b\n  HostName 10.0.0.2\n")
    by = {h.alias: h for h in read_ssh_config(config).hosts}
    assert by["a"].hostname == "10.0.0.1"
    assert by["a"].user == "after-include"  # the lines after an Include stay in its block
    assert by["b"].hostname == "10.0.0.2"


def test_an_include_inside_a_match_block_never_applies(tmp_path):
    ssh = tmp_path / ".ssh"
    _write(ssh / "m.conf", "Host *\n  User from-match\n")
    config = _write(ssh / "config", "Host a\n  HostName 1.1.1.1\nMatch exec true\n  Include m.conf\n")
    (a,) = read_ssh_config(config).hosts
    assert a.user is None


def test_a_value_after_an_include_does_not_beat_the_included_one(tmp_path):
    """review (CR-27): ssh reads in file order; the included `User first` comes first."""
    ssh = tmp_path / ".ssh"
    _write(ssh / "defaults.conf", "User first\n")
    config = _write(ssh / "config", "Host box\n  HostName 10.0.0.1\n  Include defaults.conf\n  User later\n  Port 2201\n")
    (box,) = read_ssh_config(config).hosts
    assert (box.user, box.port) == ("first", 2201)


def test_windows_paths_keep_their_backslashes(tmp_path):
    r"""review (CR-27): POSIX shlex turned C:\Users\a\.ssh\k into C:Usersa.sshk."""
    config = _write(tmp_path / ".ssh" / "config", (
        'Host w\n  IdentityFile C:\\Users\\alice\\.ssh\\id_ed25519\n'
        'Host q\n  IdentityFile "C:\\Program Files\\keys\\k"\n'
    ))
    by = {h.alias: h for h in read_ssh_config(config).hosts}
    assert by["w"].identity_file == "C:\\Users\\alice\\.ssh\\id_ed25519"
    assert by["q"].identity_file == "C:\\Program Files\\keys\\k"


def test_escapes_and_quotes_follow_openssh(tmp_path):
    from agora.ssh_config import _words

    assert _words('a\\ b "c d" \'e f\' g # tail') == ["a b", "c d", "e f", "g"]
    assert _words('x#y') == ["x#y"]  # only a # that starts a word is a comment
    assert _words('"" z') == ["", "z"]


def test_escapes_inside_quotes_match_ssh_g():
    r"""review: `"/home/me/key\"prod"` is /home/me/key"prod for ssh -G."""
    from agora.ssh_config import _words

    assert _words(r'"/home/me/key\"prod"') == ['/home/me/key"prod']
    assert _words(r'"C:\\keys\\k"') == [r"C:\keys\k"]
    assert _words(r'"C:\Users\a"') == [r"C:\Users\a"]  # a lone backslash stays
