"""Proxy manager: parsing formats, masking, backed-up writes, probes, and the tab."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import requests

from uyam import proxies_admin as pa
from uyam.sources.proxy_pool import _parse_proxy_line, to_playwright_proxy

SECRET = "s3cr3t-pass"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("gw.newprov.io:8000:alice:" + SECRET, f"http://alice:{SECRET}@gw.newprov.io:8000"),
        ("alice:" + SECRET + ":gw.newprov.io:8000", f"http://alice:{SECRET}@gw.newprov.io:8000"),
        ("alice:" + SECRET + "@gw.newprov.io:8000", f"http://alice:{SECRET}@gw.newprov.io:8000"),
        ("https://alice:pw@gw.newprov.io:443", "https://alice:pw@gw.newprov.io:443"),
        ("socks5://gw.newprov.io:1080", "socks5://gw.newprov.io:1080"),
        ("gw.newprov.io:8000", "http://gw.newprov.io:8000"),
    ],
)
def test_provider_formats_parse(line: str, expected: str) -> None:
    assert _parse_proxy_line(line) == expected


def test_special_characters_in_credentials_are_encoded_and_round_trip() -> None:
    url = _parse_proxy_line("gw.newprov.io:8000:user-zone-ph:p@ss:w0rd")
    assert url == "http://user-zone-ph:p%40ss%3Aw0rd@gw.newprov.io:8000"
    assert to_playwright_proxy(url) == {
        "server": "http://gw.newprov.io:8000",
        "username": "user-zone-ph",
        "password": "p@ss:w0rd",
    }


def test_parse_text_counts_invalid_duplicates_providers_and_socks_auth() -> None:
    text = "\n".join(
        [
            "# header",
            "a.wolveproxy.com:1:u:p",
            "a.wolveproxy.com:1:u:p",  # duplicate
            "garbage line",
            "gw.newprov.io:8000:u:p",
            "socks5://u:p@gw.newprov.io:1080",
            "",
        ]
    )
    parsed = pa.parse_text(text)
    assert len(parsed.urls) == 3
    assert parsed.duplicates == 1
    assert parsed.invalid_lines == [4]
    assert parsed.providers == {"wolveproxy.com": 1, "newprov.io": 2}
    assert parsed.socks_with_auth == 1


def test_mask_and_label_never_show_the_password() -> None:
    url = f"http://alice:{SECRET}@gw.newprov.io:8000"
    assert pa.mask(url) == "http://alice:***@gw.newprov.io:8000"
    assert pa.label(url) == "gw.newprov.io:8000"
    assert pa.provider("http://1.2.3.4:80") == "raw IP"


def _write(path: Path, *lines: str) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_replace_append_remove_restore_all_keep_backups(tmp_path: Path) -> None:
    proxies = tmp_path / "proxies.txt"
    backups = tmp_path / "data" / "proxy-backups"
    _write(proxies, "a.wolveproxy.com:1:u:p", "b.wolveproxy.com:2:u:p")

    out = pa.save(proxies, backups, pa.parse_text("gw.newprov.io:8000:u:p"), mode="replace")
    assert out["written"] == 1 and out["before"] == 2
    assert pa.read_file(proxies).providers == {"newprov.io": 1}
    assert len(pa.list_backups(backups)) == 1
    assert not proxies.with_suffix(".txt.tmp").exists()

    pa.save(
        proxies,
        backups,
        pa.parse_text("gw.newprov.io:8000:u:p\ngw2.newprov.io:8001:u:p"),
        mode="append",
    )
    assert len(pa.read_file(proxies).urls) == 2  # the existing entry is not duplicated

    with pytest.raises(ValueError, match="empty"):
        pa.save(proxies, backups, pa.parse_text(""), mode="replace")

    oldest = pa.list_backups(backups)[-1]  # the original Wolves list
    pa.restore(proxies, backups, oldest)
    assert pa.read_file(proxies).providers == {"wolveproxy.com": 2}

    removed = pa.remove_providers(proxies, backups, {"wolveproxy.com"})
    assert removed["removed"] == 2 and removed["written"] == 0

    with pytest.raises(ValueError, match="backup folder"):
        pa.restore(proxies, backups, proxies)


class _Resp:
    def __init__(self, status: int) -> None:
        self.status_code = status


def test_probe_reports_status_and_hides_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    good = "http://u:p@good.newprov.io:8000"
    bad = f"http://u:{SECRET}@bad.newprov.io:8000"

    def fake_get(target: str, proxies: dict[str, str], **_: Any) -> _Resp:
        if "bad" in proxies["https"]:
            raise requests.exceptions.ProxyError(
                f"Tunnel connection failed: 407 Proxy Authentication Required {proxies['https']}"
            )
        return _Resp(200)

    monkeypatch.setattr("uyam.proxies_admin.requests.get", fake_get)
    results = {r.proxy: r for r in pa.probe_proxies([good, bad], sample=10)}

    assert results["good.newprov.io:8000"].ok
    assert results["good.newprov.io:8000"].status == 200
    failed = results["bad.newprov.io:8000"]
    assert not failed.ok
    assert "407" in str(failed.error)
    assert SECRET not in repr(failed)


def test_probe_samples_without_exceeding_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("uyam.proxies_admin.requests.get", lambda *a, **k: _Resp(200))
    urls = [f"http://h{i}.newprov.io:8000" for i in range(30)]
    assert len(pa.probe_proxies(urls, sample=5, seed=1)) == 5
    assert pa.probe_proxies([], sample=5) == []


def test_proxies_tab_saves_a_pasted_list_without_rendering_passwords(tmp_path: Path) -> None:
    from streamlit.testing.v1 import AppTest

    proxies = tmp_path / "proxies.txt"
    backups = tmp_path / "proxy-backups"
    _write(proxies, f"a.wolveproxy.com:1:olduser:{SECRET}")

    def page(proxies_path: str, backup_dir: str) -> None:
        from pathlib import Path

        from uyam.proxies_ui import render_proxies_tab

        render_proxies_tab(Path(proxies_path), Path(backup_dir))

    at = AppTest.from_function(page, args=(str(proxies), str(backups)))
    at.run()
    assert not at.exception
    assert [m.value for m in at.metric][:2] == ["1", "1"]

    at.text_area(key="proxy_paste").input(f"gw.newprov.io:8000:newuser:{SECRET}").run()
    assert not at.exception
    at.button(key="proxy_save").click().run()
    assert not at.exception

    assert pa.read_file(proxies).providers == {"newprov.io": 1}
    assert len(pa.list_backups(backups)) == 1
    rendered = " ".join(
        str(getattr(el, "value", "")) for el in [*at.code, *at.markdown, *at.caption, *at.success]
    )
    assert "Saved 1 proxies" in rendered
    assert SECRET not in rendered
