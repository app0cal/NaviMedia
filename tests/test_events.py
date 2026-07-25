"""Tests for non-blocking live-dashboard event publication."""

import queue

from media_dl.events import EventBroker


def test_broker_publishes_compact_events_to_subscribers():
    """Verify connected subscribers receive event names and record identifiers."""
    broker = EventBroker()

    with broker.subscribe() as subscriber:
        broker.publish("playlist", playlist_id=42)
        event = subscriber.get_nowait()

    assert event.name == "playlist"
    assert event.data == {"playlist_id": 42}


def test_event_stream_starts_with_reconnect_frame():
    """Verify SSE consumers get an initial frame that triggers a REST refresh."""
    broker = EventBroker()
    stream = broker.stream()

    assert next(stream) == "event: connected\ndata: {}\n\n"
    stream.close()


def test_event_stream_heartbeats_and_cleans_up(monkeypatch):
    """Verify idle streams heartbeat and unregister when the client disconnects."""
    broker = EventBroker()

    def raise_empty(_subscriber, timeout=None):
        raise queue.Empty

    monkeypatch.setattr(queue.Queue, "get", raise_empty)
    stream = broker.stream()

    assert next(stream) == "event: connected\ndata: {}\n\n"
    assert next(stream) == ": heartbeat\n\n"
    assert len(broker._subscribers) == 1

    stream.close()
    assert not broker._subscribers


def test_slow_subscriber_does_not_block_publication():
    """Verify a full bounded queue drops excess events without growing."""
    broker = EventBroker()

    with broker.subscribe() as subscriber:
        for index in range(101):
            broker.publish("job", job_id=index)

        assert subscriber.qsize() == 100
