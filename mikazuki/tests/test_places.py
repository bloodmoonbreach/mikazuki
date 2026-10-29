from remote_eligibility import geo

from mikazuki import places
from mikazuki.store import Store


def test_downloads_missing_town_lists_once(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.write("profile.toml", 'countries = ["IN", "ES", "IN"]\n')
    monkeypatch.setenv("REMOTE_CHECK_DATA", str(tmp_path / "places"))
    calls = []

    def fake_install(codes, min_population=1000):
        calls.append(codes)
        (tmp_path / "places").mkdir(exist_ok=True)
        (tmp_path / "places" / f"cities-{codes[0]}.txt").write_text("", encoding="utf-8")
        return {codes[0]: 0}

    monkeypatch.setattr(geo, "install_country_data", fake_install)
    assert places.missing(store) == ["IN", "ES"]
    places.ensure(store, background=False)
    assert calls == [["IN"], ["ES"]]
    places.ensure(store, background=False)
    assert len(calls) == 2  # already there: nothing downloaded again


def test_offline_is_not_an_error(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.write("profile.toml", 'countries = ["PK"]\n')
    monkeypatch.setenv("REMOTE_CHECK_DATA", str(tmp_path / "places"))
    monkeypatch.setattr(geo, "install_country_data", lambda codes, min_population=1000: (_ for _ in ()).throw(OSError("offline")))
    places.ensure(store, background=False)
    assert places.missing(store) == ["PK"]


def test_background_download_off_when_scheduler_off(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.write("profile.toml", 'countries = ["PK"]\n')
    monkeypatch.setenv("MIKAZUKI_SCHEDULER", "0")
    monkeypatch.setattr(geo, "install_country_data", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no download")))
    places.ensure(store)
