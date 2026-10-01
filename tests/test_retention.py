import datetime as dt
import os
import zipfile

from mc import retention, manifest, config, update
from mc.retention import Policy, select

NOW = dt.datetime(2026, 10, 1, 12, 0, 0)


def e(ts, name=None):
    return ts, name or ts.strftime("%Y-%m-%d_%H-%M-%S.zip")


def hourly(n, step_hours=1):
    return [e(NOW - dt.timedelta(hours=i * step_hours)) for i in range(n)]


def test_empty_policy_keeps_everything():
    entries = hourly(10)
    keep, delete = select(entries, Policy(), NOW)
    assert keep == {p for _, p in entries} and not delete


def test_no_entries():
    assert select([], Policy(keep_daily=1), NOW) == (set(), set())


def test_newest_always_kept():
    old = e(dt.datetime(2020, 1, 1))
    keep, delete = select([old], Policy(keep_all_hours=1), NOW)
    assert keep == {old[1]} and not delete


def test_keep_all_hours_boundary():
    inside = e(NOW - dt.timedelta(hours=47, minutes=59))
    edge = e(NOW - dt.timedelta(hours=48))
    outside = e(NOW - dt.timedelta(hours=48, seconds=1))
    newest = e(NOW)
    keep, delete = select([inside, edge, outside, newest], Policy(keep_all_hours=48), NOW)
    assert inside[1] in keep and newest[1] in keep
    assert edge[1] in delete and outside[1] in delete


def test_daily_keeps_newest_per_day_for_n_days_with_backups():
    entries = [
        e(dt.datetime(2026, 9, 30, 1)), e(dt.datetime(2026, 9, 30, 23)),
        e(dt.datetime(2026, 9, 28, 5)),  # 29th has no backup, so 28th counts as the 2nd period
        e(dt.datetime(2026, 9, 27, 5)),
    ]
    keep, delete = select(entries, Policy(keep_daily=2), NOW)
    assert keep == {entries[1][1], entries[2][1]}
    assert delete == {entries[0][1], entries[3][1]}


def test_daily_midnight_boundary():
    a = e(dt.datetime(2026, 9, 29, 23, 59, 59))
    b = e(dt.datetime(2026, 9, 30, 0, 0, 0))
    keep, delete = select([a, b], Policy(keep_daily=2), NOW)
    assert keep == {a[1], b[1]}


def test_weekly_iso_boundary():
    # 2026-09-27 is a Sunday (end of ISO week 39), 2026-09-28 is Monday (week 40)
    sun = e(dt.datetime(2026, 9, 27, 12))
    mon = e(dt.datetime(2026, 9, 28, 12))
    mon2 = e(dt.datetime(2026, 9, 29, 12))
    keep, delete = select([sun, mon, mon2], Policy(keep_weekly=2), NOW)
    assert keep == {sun[1], mon2[1]} and delete == {mon[1]}


def test_monthly_year_boundary():
    dec = e(dt.datetime(2025, 12, 31, 23))
    jan = e(dt.datetime(2026, 1, 1, 0))
    jan2 = e(dt.datetime(2026, 1, 20, 0))
    keep, delete = select([dec, jan, jan2], Policy(keep_monthly=2), NOW)
    assert keep == {dec[1], jan2[1]} and delete == {jan[1]}


def test_yearly_and_half_yearly_and_quarterly():
    entries = [e(dt.datetime(2026, 9, 1)), e(dt.datetime(2026, 7, 1)), e(dt.datetime(2026, 6, 30)),
               e(dt.datetime(2026, 4, 1)), e(dt.datetime(2025, 3, 1))]
    keep, _ = select(entries, Policy(keep_yearly=2), NOW)
    assert keep == {entries[0][1], entries[4][1]}
    keep, _ = select(entries, Policy(keep_half_yearly=2), NOW)  # H2 2026 -> Sep 1, H1 2026 -> Jun 30
    assert keep == {entries[0][1], entries[2][1]}
    keep, _ = select(entries, Policy(keep_quarterly=3), NOW)  # Q3: Sep 1, Q2: Jun 30, then Q1 2025
    assert keep == {entries[0][1], entries[2][1], entries[4][1]}


def test_keep_last():
    entries = hourly(10)
    keep, delete = select(entries, Policy(keep_last=3), NOW)
    assert keep == {p for _, p in entries[:3]} and len(delete) == 7


def test_rules_union():
    entries = hourly(100, step_hours=1)
    keep, delete = select(entries, Policy(keep_all_hours=5, keep_last=8), NOW)
    assert len(keep) == 8


def test_policy_never_deletes_newest_and_partitions():
    entries = hourly(500, step_hours=7)
    keep, delete = select(entries, Policy(keep_all_hours=48, keep_daily=7, keep_weekly=4, keep_monthly=6,
                                          keep_yearly=2), NOW)
    assert entries[0][1] in keep
    assert keep | delete == {p for _, p in entries} and not (keep & delete)
    assert len(keep) < len(entries)


def test_parse_names():
    assert retention.parse_world_backup_name("2026-01-02_03-04-05.zip") == dt.datetime(2026, 1, 2, 3, 4, 5)
    assert retention.parse_world_backup_name("2026-01-02_03-04-05.zip.partial") is None
    assert retention.parse_world_backup_name("notes.txt") is None
    assert retention.parse_world_backup_name("2026-13-02_03-04-05.zip") is None
    assert retention.parse_update_backup_name("2026-01-02_03-04-05_1.21.1_to_1.21.2.zip") == dt.datetime(
        2026, 1, 2, 3, 4, 5)
    assert retention.parse_update_backup_name("1.21.1_to_1.21.2.zip") is None  # no path -> cannot use mtime


def test_parse_legacy_update_name_uses_mtime(tmp_path):
    f = tmp_path / "1.0_to_1.1.zip"
    f.write_bytes(b"x")
    ts = dt.datetime(2024, 5, 5, 5, 5, 5)
    os.utime(f, (ts.timestamp(), ts.timestamp()))
    assert retention.parse_update_backup_name(f.name, str(f)) == ts


def test_prune_dir_ignores_unknown_and_partial_and_dirs(tmp_path):
    names = [(NOW - dt.timedelta(days=d)).strftime("%Y-%m-%d_%H-%M-%S.zip") for d in range(10)]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    (tmp_path / "keepme.txt").write_text("x")
    (tmp_path / "2000-01-01_00-00-00.zip.partial").write_text("x")
    (tmp_path / "last_manifest.json").write_text("{}")
    (tmp_path / "sub.zip").mkdir()
    deleted = retention.prune_dir(str(tmp_path), retention.parse_world_backup_name, Policy(keep_last=2), now=NOW)
    assert len(deleted) == 8
    remaining = set(os.listdir(tmp_path))
    assert {"keepme.txt", "2000-01-01_00-00-00.zip.partial", "last_manifest.json", "sub.zip"} <= remaining
    assert names[0] in remaining and names[1] in remaining


def test_prune_dir_dry_run_deletes_nothing(tmp_path):
    for d in range(5):
        (tmp_path / (NOW - dt.timedelta(days=d)).strftime("%Y-%m-%d_%H-%M-%S.zip")).write_bytes(b"x")
    deleted = retention.prune_dir(str(tmp_path), retention.parse_world_backup_name, Policy(keep_last=1), dry_run=True,
                                  now=NOW)
    assert len(deleted) == 4 and len(os.listdir(tmp_path)) == 5


def test_prune_dir_missing_dir():
    assert retention.prune_dir("does-not-exist-xyz", retention.parse_world_backup_name, Policy(keep_last=1)) == []


def test_config_invalid_values_fall_back(monkeypatch):
    monkeypatch.setenv("MC_BACKUP_KEEP_DAILY", "abc")
    monkeypatch.setenv("MC_BACKUP_KEEP_WEEKLY", "-3")
    monkeypatch.setenv("MC_BACKUP_KEEP_MONTHLY", "9")
    p = config.get_world_backup_policy()
    assert p.keep_daily == 7 and p.keep_weekly == 4 and p.keep_monthly == 9
    monkeypatch.setenv("MC_KEEP_DOWNLOADED_VERSIONS", "0")
    assert config.get_keep_downloaded_versions() == 5


def test_manifest_detects_changes_and_ignores(tmp_path):
    world = tmp_path / "w"
    (world / "db").mkdir(parents=True)
    (world / "level.dat").write_bytes(b"abc")
    (world / "db" / "LOG").write_bytes(b"1")
    m1 = manifest.build_manifest(str(world))
    assert m1 == manifest.build_manifest(str(world))
    (world / "db" / "LOG").write_bytes(b"12")
    m2 = manifest.build_manifest(str(world))
    assert m1 != m2
    assert manifest.build_manifest(str(world), ["LOG"]) == manifest.build_manifest(str(world), ["db/LOG"])
    assert "db/LOG" not in manifest.build_manifest(str(world), ["LOG"])
    mf = tmp_path / "m.json"
    manifest.save_manifest(str(mf), m2)
    assert manifest.load_manifest(str(mf)) == m2
    assert manifest.load_manifest(str(tmp_path / "nope.json")) is None


def test_update_backup_excludes_vendor_binaries(tmp_path):
    cur = tmp_path / "current"
    (cur / "worlds" / "w").mkdir(parents=True)
    (cur / "worlds" / "w" / "level.dat").write_bytes(b"x")
    (cur / "bedrock_server.exe").write_bytes(b"x")
    (cur / "server.properties").write_text("level-name=w")
    (cur / "worlds" / "tool.exe").write_bytes(b"x")  # not at root, kept
    out = tmp_path / "out.zip"
    update._backup_current_for_update(str(cur), str(out))
    with zipfile.ZipFile(out) as z:
        names = {n.replace("\\", "/") for n in z.namelist()}
    assert names == {"worlds/w/level.dat", "server.properties", "worlds/tool.exe"}
    assert not os.path.exists(str(out) + ".partial")


def test_world_backup_skips_unchanged_and_leaves_no_partial(tmp_path, monkeypatch):
    from mc import paths
    from mc.server_runtime import ServerRuntime

    server = tmp_path / "server"
    (server / "worlds" / "w").mkdir(parents=True)
    (server / "worlds" / "w" / "level.dat").write_bytes(b"abc")
    (server / "bedrock_server.exe").write_bytes(b"")
    (server / "server.properties").write_text("level-name=w\n")
    backups = tmp_path / "backups"
    monkeypatch.setattr(paths, "_path_to_backup_dir", str(backups))

    rt = ServerRuntime(str(server / "bedrock_server.exe"))
    assert rt._backup_world_files() is True
    assert rt._backup_world_files() is False  # unchanged
    (server / "worlds" / "w" / "level.dat").write_bytes(b"abcd")
    import time
    time.sleep(1.1)  # file names have one second resolution
    assert rt._backup_world_files() is True
    names = os.listdir(backups / "w")
    assert len([n for n in names if n.endswith(".zip")]) == 2
    assert not [n for n in names if n.endswith(".partial")]
    assert "last_manifest.json" in names
