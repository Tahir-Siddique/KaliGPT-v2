"""Metasploit lab helpers (status / catalog search only)."""

from agents.utils.tools.metasploit import metasploit_search, metasploit_status


def test_status_when_missing(monkeypatch):
    monkeypatch.setattr("agents.utils.tools.metasploit._which", lambda name: None)
    info = metasploit_status()
    assert info["installed"] is False
    assert info["msfconsole"] is None
    assert "Kali" in (info["hint"] or "")


def test_status_when_present(monkeypatch):
    monkeypatch.setattr(
        "agents.utils.tools.metasploit._which",
        lambda name: f"/usr/bin/{name}" if name in {"msfconsole", "msfvenom"} else None,
    )
    monkeypatch.setattr(
        "agents.utils.tools.metasploit._run",
        lambda argv, timeout=20: (0, "6.4.0-dev\n", ""),
    )
    info = metasploit_status()
    assert info["installed"] is True
    assert info["msfconsole"] == "/usr/bin/msfconsole"
    assert "6.4.0" in (info["version"] or "")


def test_search_rejects_unsafe_query(monkeypatch):
    monkeypatch.setattr(
        "agents.utils.tools.metasploit.metasploit_status",
        lambda: {"msfconsole": "/usr/bin/msfconsole", "installed": True},
    )
    bad = metasploit_search("ftp; run")
    assert bad["ok"] is False
    assert bad["matches"] == []


def test_search_parses_module_names(monkeypatch):
    monkeypatch.setattr(
        "agents.utils.tools.metasploit.metasploit_status",
        lambda: {"msfconsole": "/usr/bin/msfconsole", "installed": True},
    )
    listing = (
        "Matching Modules\n"
        "   0  auxiliary/scanner/ftp/ftp_version  Normal  FTP Version Scanner\n"
        "   1  auxiliary/scanner/smb/smb_version  Normal  SMB Version Detection\n"
    )
    monkeypatch.setattr(
        "agents.utils.tools.metasploit._run",
        lambda argv, timeout=20: (0, listing, ""),
    )
    result = metasploit_search("ftp")
    assert result["ok"] is True
    assert "auxiliary/scanner/ftp/ftp_version" in result["matches"]
    assert "note" in result
