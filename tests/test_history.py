from conftest import headers, new_session

from proctor.domain import utc_add, utcnow

P = "/api/v1/proctoring"


def test_proctor_can_read_verification_reasons_but_outsider_cannot(client, raw_jpeg):
    sid, h = new_session(client)
    client.post(f"{P}/sessions/{sid}/precheck", headers=h, files={"image": ("x.jpg", raw_jpeg)})
    client.post(f"{P}/sessions/{sid}/verify", headers=h, files={"image": ("x.jpg", raw_jpeg)})
    r = client.get(f"{P}/sessions/{sid}/verification-history", headers=headers(client, "proctor_a"))
    assert r.status_code == 200
    assert len(r.json()["prechecks"]) == 1
    assert r.json()["attempts"][0]["reason"] == "REFERENCE_PROFILE_MISSING"
    assert (
        client.get(
            f"{P}/sessions/{sid}/verification-history", headers=headers(client, "outsider")
        ).status_code
        == 404
    )


def test_three_eligible_no_matches_require_manual_review(client, raw_jpeg):
    sid, h = new_session(client)
    enroll = client.post(
        P + "/profiles/demo-candidate-a",
        headers=headers(client, "admin"),
        files={"image": ("ref.jpg", raw_jpeg)},
        data={"source_ref": "synthetic-test-reference", "expires_at": utc_add(utcnow(), 86400)},
    )
    assert enroll.status_code == 200
    client.models.vector *= -1
    client.post(f"{P}/sessions/{sid}/precheck", headers=h, files={"image": ("x.jpg", raw_jpeg)})
    for _ in range(3):
        r = client.post(f"{P}/sessions/{sid}/verify", headers=h, files={"image": ("x.jpg", raw_jpeg)})
        assert r.json()["result"] == "NO_MATCH"
    assert r.json()["manual_review_required"]
    assert (
        client.post(f"{P}/sessions/{sid}/verify", headers=h, files={"image": ("x.jpg", raw_jpeg)}).status_code
        == 409
    )


def test_manual_retry_same_key_returns_same_decision_without_extra_history(client, raw_jpeg):
    sid, h = new_session(client)
    client.post(f"{P}/sessions/{sid}/precheck", headers=h, files={"image": ("x.jpg", raw_jpeg)})
    proctor = headers(client, "proctor_a", "repeat-manual")
    body = {
        "decision": "ACCEPT_MANUALLY",
        "reason": "Đã kiểm tra nguồn chuẩn trực tiếp",
        "expected_version": 1,
    }
    a = client.post(f"{P}/sessions/{sid}/manual-decision", headers=proctor, json=body)
    b = client.post(f"{P}/sessions/{sid}/manual-decision", headers=proctor, json=body)
    assert a.status_code == b.status_code == 200
    assert client.service.store.row("SELECT count(*) n FROM manual_decisions")["n"] == 1
