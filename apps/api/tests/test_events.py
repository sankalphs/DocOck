from __future__ import annotations

import asyncio

from app.events.bus import EventBus


def test_publish_assigns_monotonic_ids() -> None:
    bus = EventBus()
    first = bus.publish("fault.injected", {"fault_id": 1})
    second = bus.publish("incident.opened", {"incident_id": "INC-00001"})
    assert (first.id, second.id) == (1, 2)
    assert bus.last_id == 2


def test_replay_returns_events_after_id() -> None:
    bus = EventBus()
    first = bus.publish("fault.injected")
    second = bus.publish("fault.reverted")
    assert [e.id for e in bus.replay(None)] == [first.id, second.id]
    assert [e.id for e in bus.replay(first.id)] == [second.id]
    assert bus.replay(second.id) == []


def test_history_is_bounded() -> None:
    bus = EventBus(history=2)
    bus.publish("a")
    bus.publish("b")
    bus.publish("c")
    assert [event.type for event in bus.replay(None)] == ["b", "c"]


async def test_subscriber_receives_new_events_only() -> None:
    bus = EventBus()
    bus.publish("before.subscribe")
    queue = bus.subscribe()
    published = bus.publish("incident.opened", {"id": 7})
    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received.id == published.id
    assert received.type == "incident.opened"
    bus.unsubscribe(queue)
    assert bus.subscriber_count == 0


def test_sse_frame_shape() -> None:
    bus = EventBus()
    event = bus.publish("agent.step", {"seq": 1, "tool": "get_traces"})
    frame = event.to_sse()
    assert frame.startswith(f"id: {event.id}\nevent: agent.step\ndata: ")
    assert frame.endswith("\n\n")
    assert '"tool":"get_traces"' in frame


def test_full_subscriber_queue_drops_oldest() -> None:
    bus = EventBus()
    queue = bus.subscribe()
    for index in range(300):
        bus.publish("tick", {"index": index})
    assert queue.qsize() <= queue.maxsize
    bus.unsubscribe(queue)
