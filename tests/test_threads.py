"""Tests for signed thread IDs. Pure logic, no network."""

import importlib
import logging

import pytest

from assistant import config, threads


def test_an_issued_id_verifies_to_its_key():
    signed_id, key = threads.issue()

    assert threads.verify(signed_id) == key
    assert signed_id.startswith(key + ".")


def test_issued_ids_are_unique():
    assert threads.issue()[1] != threads.issue()[1]


@pytest.mark.parametrize("value", ["", "no-separator", "key.", ".sig", "key.sig", "a.b.c"])
def test_malformed_or_unsigned_ids_do_not_verify(value):
    assert threads.verify(value) is None


def test_a_tampered_signature_does_not_verify():
    signed_id, _ = threads.issue()
    last = signed_id[-1]
    tampered = signed_id[:-1] + ("A" if last != "A" else "B")

    assert threads.verify(tampered) is None


def test_an_id_signed_with_another_secret_does_not_verify(monkeypatch):
    other_id, _ = threads.issue()

    monkeypatch.setattr(threads, "_secret", b"x" * 32)

    assert threads.verify(other_id) is None


def test_a_configured_secret_is_used_and_must_be_long_enough(monkeypatch):
    monkeypatch.setattr(config.settings, "thread_id_secret", "s" * 32)
    assert threads._load_secret() == b"s" * 32

    monkeypatch.setattr(config.settings, "thread_id_secret", "too-short")
    with pytest.raises(ValueError):
        threads._load_secret()


def test_a_missing_secret_warns_and_falls_back_to_a_random_one(monkeypatch, caplog):
    monkeypatch.setattr(config.settings, "thread_id_secret", "")

    with caplog.at_level(logging.WARNING, logger="ai-starter"):
        first, second = threads._load_secret(), threads._load_secret()

    assert first != second and len(first) == 32
    assert "THREAD_ID_SECRET is not set" in caplog.text


def test_ids_survive_a_reload_when_the_secret_is_configured(monkeypatch):
    # With THREAD_ID_SECRET set, a restart (here, a module reload) keeps
    # existing IDs valid; without it, they are all invalidated.
    monkeypatch.setattr(config.settings, "thread_id_secret", "k" * 40)
    importlib.reload(threads)
    signed_id, key = threads.issue()

    importlib.reload(threads)
    try:
        assert threads.verify(signed_id) == key
    finally:
        monkeypatch.setattr(config.settings, "thread_id_secret", "")
        importlib.reload(threads)
