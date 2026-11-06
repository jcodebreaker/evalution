"""The storage layer under every store: documents, entries, workspaces and encrypted secrets."""

import threading

import pytest

from evalution.storage import LOCAL, Database
from evalution.storage.database import SCHEMA, StorageError, schema
from evalution.storage.secrets import KEY_ENV, SecretKeyError, Secrets


def test_documents_entries_and_workspaces_keep_apart(tmp_path) -> None:
    database = Database.open(tmp_path)
    docs = database.docs()
    assert docs.workspace == LOCAL and (tmp_path / "evalution.db").is_file()
    assert Database.open(tmp_path) is database, "one pool per database in a process"
    assert (tmp_path / "evalution.db").stat().st_mode & 0o777 == 0o600, "it holds prompts"

    docs.put("run", "r1", {"run": "r1", "score": 0.5})
    docs.put("run", "r1", {"run": "r1", "score": 1})
    docs.put("profile", "app/profile.md", "Plans trips\n")
    docs.put("profile", "app_x/profile.md", "other")
    docs.put("profile", "app/drift.json", {"changes": []})
    assert docs.get("run", "r1") == {"run": "r1", "score": 1}
    assert docs.get("profile", "app/profile.md") == "Plans trips\n", "text stays text"
    assert docs.get("run", "nope") is None
    assert docs.keys("profile", "app/") == ["app/drift.json", "app/profile.md"]
    assert [k for k, _, _ in docs.items("profile", "app")] == [
        "app/drift.json",
        "app/profile.md",
        "app_x/profile.md",
    ]
    # A prefix is literal: "_" and "%" are not patterns.
    assert docs.keys("profile", "app_") == ["app_x/profile.md"]
    assert docs.keys("profile", "app%") == []

    other = database.docs("w2")
    assert other.get("run", "r1") is None and other.keys("profile") == []
    other.put("run", "r1", {"theirs": True})
    assert docs.get("run", "r1") == {"run": "r1", "score": 1}

    first = docs.append("evidence", "r1", {"try": 1})
    docs.append("evidence", "r1", {"try": 2})
    other.append("evidence", "r1", {"theirs": True})
    assert [e.body for e in docs.entries("evidence", "r1")] == [{"try": 1}, {"try": 2}]
    assert docs.entries("evidence", "r1")[0].id == first
    assert docs.clear_entries("evidence", "r1") == 2 and docs.entries("evidence", "r1") == []
    assert len(other.entries("evidence", "r1")) == 1

    assert docs.delete_prefix("profile", "app/") == 2
    assert docs.keys("profile") == ["app_x/profile.md"]
    assert docs.delete("run", "r1") and not docs.delete("run", "r1")

    # What a second process opening the same file sees.
    database.close()
    again = Database.open(tmp_path).docs()
    assert again.keys("profile") == ["app_x/profile.md"]


def test_writes_from_many_threads_all_land(tmp_path) -> None:
    docs = Database.open(tmp_path).docs()

    made: dict[int, list[int]] = {i: [] for i in range(20)}

    def write(n: int) -> None:
        for i in range(20):
            docs.put("doc", f"k{i % 5}", {"n": n, "i": i})
            docs.append("log", "one", n)
            # Only one of the writers makes a document that is created, never replaced.
            if docs.create("new", f"c{i}", n):
                made[i].append(n)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(docs.keys("doc")) == 5
    assert len(docs.entries("log", "one")) == 160
    assert all(made[i] == [docs.get("new", f"c{i}")] for i in range(20))
    assert not docs.create("doc", "k0", "late") and docs.get("doc", "k0") != "late"


def test_a_newer_database_is_refused(tmp_path) -> None:
    database = Database.open(tmp_path)
    with database.engine.begin() as db:
        db.execute(schema.insert().values(version=SCHEMA + 1))
    database.close()
    with pytest.raises(StorageError, match="newer evalution"):
        Database.open(tmp_path)


def test_secrets_are_encrypted_and_tied_to_their_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    docs = Database.open(tmp_path).docs()
    secrets = Secrets(docs, tmp_path)
    key_file = tmp_path / "secret.key"
    assert key_file.stat().st_mode & 0o777 == 0o600

    secrets.set("model.api_key", "sk-test-123456789")
    stored = docs.get("secret", "model.api_key")
    assert "123456789" not in stored, "never stored in the clear"
    assert secrets.get("model.api_key") == "sk-test-123456789"
    assert secrets.hint("model.api_key") == "…6789"
    assert Secrets(docs, tmp_path).get("model.api_key") == "sk-test-123456789", "same key file"
    assert secrets.names() == ["model.api_key"]

    secrets.set("model.api_key", None)
    assert secrets.get("model.api_key") is None and secrets.hint("model.api_key") is None

    # Another key can't read them, and says so instead of returning garbage.
    secrets.set("t.key", "value-1234")
    from cryptography.fernet import Fernet

    monkeypatch.setenv(KEY_ENV, Fernet.generate_key().decode())
    with pytest.raises(SecretKeyError):
        Secrets(docs, tmp_path).get("t.key")
    monkeypatch.setenv(KEY_ENV, "not-a-key")
    with pytest.raises(SecretKeyError):
        Secrets(docs, tmp_path)
