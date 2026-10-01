"""URL validation, snapshot persistence and expiry.

The API is public, so this is the boundary that has to hold. Everything here is
about what happens when a request is hostile or when the process restarts:
URLs that must never reach git, paths that must never escape the clone, and
snapshots that have to survive a restart because a judge's chat arrives after
their analysis.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from app.core.chat_context import read_repo_file, resolve_repo_file
from app.core.ingestion import (
    IngestionEngine,
    RepoURLError,
    normalize_repo_url,
)


# --------------------------------------------------------------------------- #
# URL validation
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        "https://github.com/pallets/flask",
        "https://github.com/pallets/flask.git",
        "https://github.com/pallets/flask/",
        "https://GitHub.com/Pallets/Flask",
        "  https://github.com/pallets/flask  ",
        "https://www.github.com/pallets/flask",
        "git@github.com:pallets/flask.git",
    ],
)
def test_urls_that_name_a_github_repo_are_accepted(raw: str):
    """A judge will paste whatever their browser shows; all of these are it."""
    assert normalize_repo_url(raw) == "https://github.com/pallets/flask"


@pytest.mark.parametrize(
    ("raw", "because"),
    [
        ("", "empty"),
        ("   ", "blank"),
        ("http://github.com/pallets/flask", "plain http"),
        ("git://github.com/pallets/flask", "the git protocol"),
        ("file:///etc/passwd", "a local path"),
        ("https://gitlab.com/pallets/flask", "another forge"),
        ("https://github.com.evil.com/pallets/flask", "a lookalike host"),
        ("https://github.com:pallets/flask", "a port, not a path"),
        ("https://user:pass@github.com/pallets/flask", "credentials in the URL"),
        ("https://github.com/pallets/flask?x=1", "a query string"),
        ("https://github.com/pallets/flask#readme", "a fragment"),
        ("https://github.com/pallets", "no repository"),
        ("https://github.com/pallets/flask/tree/main/src", "a subdirectory"),
        ("https://github.com/pallets/flask/../../etc", "traversal"),
        ("https://github.com/pallets/flask.git;rm -rf /", "a shell fragment"),
        ("https://github.com/../etc/passwd", "a traversal owner"),
        ("https://github.com/-oProxyCommand=x", "a dash-prefixed name"),
        ("https://github.com/pallets/.git", "a hidden name"),
        ("https://github.com/--upload-pack=id", "a git option as a name"),
        ("javascript:alert(1)", "a script URL"),
    ],
)
def test_urls_that_are_not_a_github_repo_are_refused(raw: str, because: str):
    with pytest.raises(RepoURLError):
        normalize_repo_url(raw)


def test_error_messages_do_not_echo_a_hostile_url():
    with pytest.raises(RepoURLError) as raised:
        normalize_repo_url("https://github.com.evil.com/a/b")
    assert "evil.com" not in str(raised.value)


def test_the_same_repository_gets_the_same_id_however_it_is_spelled(data_dir: str):
    engine = IngestionEngine()
    assert engine.repo_id_for("https://github.com/pallets/flask") == engine.repo_id_for(
        "https://github.com/pallets/flask.git"
    )
    assert engine.repo_id_for("https://github.com/pallets/flask") != engine.repo_id_for("https://github.com/pallets/django")


def test_an_unparseable_url_still_gets_a_usable_id_rather_than_an_exception(data_dir: str):
    """Identity must not be the thing that fails; validation runs elsewhere."""
    identifier = IngestionEngine().repo_id_for("not a url at all")
    assert identifier.isalnum() and len(identifier) == 12


# --------------------------------------------------------------------------- #
# path safety
# --------------------------------------------------------------------------- #


def test_a_file_inside_the_repo_resolves(repo: str):
    absolute, relative = resolve_repo_file(repo, "pkg/app.py")
    assert relative == os.path.join("pkg", "app.py")
    assert open(absolute, encoding="utf-8").read().startswith("import os")


@pytest.mark.parametrize(
    "attempt",
    [
        "../../etc/passwd",
        "../../../etc/passwd",
        "pkg/../../../etc/passwd",
        "./../../etc/passwd",
        "/etc/passwd",
        "/etc/passwd".lstrip("/"),
        "pkg/../../../../../../etc/hosts",
    ],
)
def test_traversal_attempts_resolve_to_nothing(repo: str, attempt: str):
    assert resolve_repo_file(repo, attempt) == ("", "")


def test_traversal_through_a_symlink_is_refused(tmp_path, repo: str):
    """``realpath`` follows the link before the containment check, which is the point."""
    link = os.path.join(repo, "escape")
    os.symlink(tmp_path, link)
    assert resolve_repo_file(repo, "escape/secret.txt") == ("", "")
    assert read_repo_file(repo, "escape/secret.txt") == ""


def test_a_bare_filename_outside_is_not_reachable(repo: str, outside: str):
    name = os.path.basename(outside)
    assert resolve_repo_file(repo, name) == ("", "")


def test_directories_and_missing_files_resolve_to_nothing(repo: str):
    assert resolve_repo_file(repo, "pkg") == ("", "")
    assert resolve_repo_file(repo, "nope.py") == ("", "")


def test_empty_inputs_resolve_to_nothing(repo: str):
    assert resolve_repo_file(repo, "") == ("", "")
    assert resolve_repo_file("", "pkg/app.py") == ("", "")
    assert resolve_repo_file("/nonexistent-root", "pkg/app.py") == ("", "")


def test_reading_is_capped_at_the_byte_budget(repo: str):
    assert len(read_repo_file(repo, "pkg/app.py", 10)) == 10
    assert read_repo_file(repo, "pkg/app.py", 10_000).count("\n") == 5
    assert read_repo_file(repo, "nope.py") == ""


# --------------------------------------------------------------------------- #
# snapshot persistence
# --------------------------------------------------------------------------- #


def snapshot(**overrides) -> dict:
    payload = {
        "repo_url": "https://github.com/pallets/flask",
        "head_sha": "a" * 40,
        "analyzed_at": "2026-09-01T10:00:00Z",
        "repo_path": "",
        "files": [],
        "graph": {"nodes": [], "links": []},
    }
    payload.update(overrides)
    return payload


def test_a_snapshot_survives_a_new_engine(data_dir: str):
    """The restart case: a judge's chat lands after the process that analysed."""
    IngestionEngine().save_snapshot("abc123", snapshot())

    reopened = IngestionEngine()
    stored = reopened.load_snapshot("abc123")

    assert stored is not None
    assert stored["head_sha"] == "a" * 40
    assert stored["files"] == []


def test_the_index_maps_a_url_to_its_snapshot(data_dir: str):
    engine = IngestionEngine()
    repo_id = engine.repo_id_for("https://github.com/pallets/flask")
    engine.save_snapshot(repo_id, snapshot())

    with open(engine.index_path, encoding="utf-8") as handle:
        index = json.load(handle)
    assert index["https://github.com/pallets/flask"]["repo_id"] == repo_id
    assert index["https://github.com/pallets/flask"]["head_sha"] == "a" * 40


def test_snapshots_are_found_by_name_when_the_index_is_lost(data_dir: str):
    engine = IngestionEngine()
    engine.save_snapshot("abc123", snapshot())
    os.remove(engine.index_path)
    os.remove(engine.registry_path)

    assert IngestionEngine().load_snapshot("abc123") is not None


def test_a_corrupt_snapshot_reads_as_missing_rather_than_crashing(data_dir: str):
    engine = IngestionEngine()
    with open(engine._snapshot_path("broken"), "w", encoding="utf-8") as handle:
        handle.write("{not json")

    assert engine.load_snapshot("broken") is None
    assert engine.load_snapshot("never-written") is None


def test_no_temporary_file_is_left_behind_by_a_write(data_dir: str):
    engine = IngestionEngine()
    engine.save_snapshot("abc123", snapshot())
    engine.save_snapshot("def456", snapshot(head_sha="b" * 40))

    leftovers = [name for name in os.listdir(data_dir) if name.startswith(".tmp-")]
    assert leftovers == []


def test_a_failed_write_leaves_no_partial_snapshot(data_dir: str, monkeypatch):
    engine = IngestionEngine()

    def explode(payload, _handle):
        raise RuntimeError("disk full")

    monkeypatch.setattr("app.core.ingestion.json.dump", explode)
    with pytest.raises(RuntimeError):
        engine.save_snapshot("abc123", snapshot())

    assert engine.load_snapshot("abc123") is None
    assert [name for name in os.listdir(data_dir) if name.startswith(".tmp-")] == []


def test_the_data_directory_is_created_if_it_does_not_exist(tmp_path):
    target = tmp_path / "deep" / "nested" / "data"
    IngestionEngine(str(target))
    assert target.is_dir()


# --------------------------------------------------------------------------- #
# expiry
# --------------------------------------------------------------------------- #


def test_snapshots_past_their_time_to_live_are_deleted(data_dir: str, monkeypatch):
    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "24")
    IngestionEngine().save_snapshot("old1", snapshot())
    IngestionEngine().save_snapshot("old2", snapshot(head_sha="b" * 40))

    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "0")
    engine = IngestionEngine()
    assert engine.expire() == ["old1", "old2"]

    assert engine.load_snapshot("old1") is None
    assert engine.load_snapshot("old2") is None


def test_a_fresh_snapshot_survives_expiry(data_dir: str, monkeypatch):
    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "24")
    engine = IngestionEngine()
    engine.save_snapshot("fresh", snapshot())

    assert engine.expire() == []
    assert engine.load_snapshot("fresh") is not None


def test_the_snapshot_being_written_is_never_a_candidate(data_dir: str, monkeypatch):
    """Otherwise a request could delete its own analysis as it finished writing it."""
    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "0")
    engine = IngestionEngine()
    engine.save_snapshot("mine", snapshot())

    assert engine.load_snapshot("mine") is not None
    assert engine.expire(keep_repo_id="mine") == []


def test_expiry_takes_the_clone_with_the_snapshot(data_dir: str, monkeypatch):
    """A snapshot without its files breaks /repo/file and the chat tools."""
    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "24")
    clone = os.path.join(data_dir, "flask_abc123")
    os.makedirs(clone)
    with open(os.path.join(clone, "app.py"), "w", encoding="utf-8") as handle:
        handle.write("x = 1\n")
    IngestionEngine().save_snapshot("abc123", snapshot(repo_path=clone))

    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "0")
    IngestionEngine().expire()

    assert not os.path.isdir(clone)


def test_expiry_never_removes_the_data_directory_itself(data_dir: str, monkeypatch):
    """A snapshot pointing at the data directory must not make `expire` delete the cache."""
    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "24")
    IngestionEngine().save_snapshot("bad", snapshot(repo_path=data_dir))

    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "0")
    IngestionEngine().expire()

    assert os.path.isdir(data_dir)
    assert os.path.exists(os.path.join(data_dir, "_registry.json"))


def test_the_count_cap_drops_the_oldest_first(data_dir: str, monkeypatch):
    monkeypatch.setenv("CODELENS_MAX_SNAPSHOTS", "2")
    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "24")
    engine = IngestionEngine()

    for index in range(3):
        engine.save_snapshot(f"repo{index}", snapshot(head_sha=str(index) * 40))
        time.sleep(0.01)

    assert engine.load_snapshot("repo0") is None
    assert engine.load_snapshot("repo1") is not None
    assert engine.load_snapshot("repo2") is not None


def test_expired_entries_leave_the_indexes_consistent(data_dir: str, monkeypatch):
    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "24")
    IngestionEngine().save_snapshot("gone", snapshot())

    monkeypatch.setenv("CODELENS_SNAPSHOT_TTL_HOURS", "0")
    IngestionEngine().expire()

    with open(IngestionEngine().index_path, encoding="utf-8") as handle:
        index = json.load(handle)
    with open(IngestionEngine().registry_path, encoding="utf-8") as handle:
        registry = json.load(handle)

    assert index == {}
    assert registry == {}


def test_stats_describe_what_is_stored(data_dir: str):
    engine = IngestionEngine()
    clone = os.path.join(data_dir, "flask_abc123")
    os.makedirs(clone)
    with open(os.path.join(clone, "app.py"), "w", encoding="utf-8") as handle:
        handle.write("x = 1\n")
    engine.save_snapshot("abc123", snapshot(repo_path=clone))
    stats = engine.stats()

    assert stats["snapshots"] == 1
    assert stats["clones"] == 1
    assert stats["bytes"] > 0
    assert stats["data_dir"] == data_dir
    assert stats["snapshot_ttl_seconds"] > 0


def test_a_truncated_analysis_is_not_served_from_cache(data_dir: str, monkeypatch):
    """Truncated means partial; serving it again would present a partial answer as final."""
    engine = IngestionEngine()
    engine.save_snapshot("part", snapshot(truncated=True))
    monkeypatch.setattr(engine, "remote_head", lambda _url: "a" * 40)

    assert engine.cached_snapshot("https://github.com/pallets/flask") is None


def test_a_stale_commit_is_re_analysed(data_dir: str, monkeypatch):
    engine = IngestionEngine()
    engine.save_snapshot("stale", snapshot(head_sha="old" * 10))
    monkeypatch.setattr(engine, "remote_head", lambda _url: "new" * 10)

    assert engine.cached_snapshot("https://github.com/pallets/flask") is None


def test_a_current_commit_is_served_from_cache(data_dir: str, repo: str, monkeypatch):
    engine = IngestionEngine()
    engine.save_snapshot("good", snapshot(repo_path=repo))
    monkeypatch.setattr(engine, "remote_head", lambda _url: "a" * 40)

    cached = engine.cached_snapshot("https://github.com/pallets/flask")
    assert cached is not None
    assert cached["head_sha"] == "a" * 40


def test_cache_lookup_refuses_a_url_it_would_not_clone(data_dir: str):
    assert IngestionEngine().cached_snapshot("https://gitlab.com/a/b") is None
