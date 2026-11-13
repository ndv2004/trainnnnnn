from conftest import headers, new_session

from proctor.domain import Change, EngineEvent, uid, utc_add, utcnow


def test_inference_evidence_update_cannot_overwrite_committed_human_review(client, raw_jpeg, monkeypatch):
    sid, _ = new_session(client)
    service = client.service
    session = service.store.session(sid)
    stamp = utcnow()
    eid = uid()
    event = EngineEvent(eid, "PHONE_VISIBLE", 0, 1, stamp, stamp, 1, stamp)
    service.persist_changes(session, [Change("create", event)], raw_jpeg)
    actor = service.security.decode(headers(client, "proctor_a")["Authorization"][7:])
    original = service.vault.write
    once = [False]

    def concurrent_review(*args):
        if not once[0]:
            once[0] = True
            service.review(
                service.store.event(eid),
                actor,
                "DISMISSED",
                "Đã xem xét trong lúc worker lưu ảnh",
                1,
                "race-review",
            )
        return original(*args)

    monkeypatch.setattr(service.vault, "write", concurrent_review)
    event.last_positive = 12
    event.last_observed_at = utc_add(stamp, 12)
    service.persist_changes(session, [Change("update", event)], raw_jpeg)
    current = service.store.event(eid)
    assert current["review_status"] == "DISMISSED"
    assert current["version"] == 3
    assert len(current["evidence_ids"]) == 2
