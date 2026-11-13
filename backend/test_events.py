import db
import events
import fixtures
import svc


def test_order_health_prioritizes_actionable_states():
    now = 1_800_000_000
    cases = [
        ({"status": "in_production", "due_at": now - 1, "delivery": {"blocked": {"reason": "x"}}}, "Late"),
        ({"status": "blocked"}, "Blocked"),
        ({"status": "in_production", "risk": {"shortfall": 4}}, "At risk"),
        ({"status": "submitted", "needs_leader": True}, "Waiting"),
        ({"status": "in_production", "started_at": now - 25 * 3600}, "Stale"),
        ({"status": "in_production", "started_at": now - 60}, "On track"),
        ({"status": "closed", "due_at": now - 100}, "On track"),
    ]
    for data, expected in cases:
        assert svc.order_health(data, now) == expected


def test_stream_projection_is_role_scoped_and_buyer_safe(world):
    buyer, leader, member, other = (world.login(phone) for phone in (
        "+919000000002", "+919000000001", "+919000000100", "+919000000101"))
    group_id = buyer.get("/api/buyer/groups").json()[0]["id"]
    order_id = buyer.post("/api/buyer/orders", json={
        "group_id": group_id, "text": fixtures.DATA["order_text"]
    }).json()["id"]
    leader.post(f"/api/leader/orders/{order_id}/quote", json={})
    buyer.post(f"/api/buyer/orders/{order_id}/accept")
    leader.post(f"/api/leader/orders/{order_id}/plan")
    leader.post(f"/api/leader/orders/{order_id}/start")
    member.post(f"/api/seller/orders/{order_id}/done", json={"n": 7})
    member_id = member.get("/api/me").json()["id"]
    other_id = other.get("/api/me").json()["id"]
    leader_user = leader.get("/api/me").json()
    buyer_user = buyer.get("/api/me").json()
    member_user = member.get("/api/me").json()
    other_user = other.get("/api/me").json()
    officer_user = world.login("+919000000009").get("/api/me").json()

    with db.S() as session:
        row = session.query(db.LedgerEvent).filter_by(type="progress.reported", order_id=order_id).one()
        order = session.get(db.Order, order_id)

        assert events.project(row, leader_user, order)["type"] == "progress.reported"
        own = events.project(row, member_user, order)
        assert own and own["summary"] == "Your work was updated"
        assert events.project(row, other_user, order) is None
        buyer_view = events.project(row, buyer_user, order)
        assert buyer_view["summary"] == "Your order is about 0% complete"
        assert not any(key in buyer_view for key in ("payload", "actor", "hash", "phone", "pay"))
        assert member_id != other_id

        officer_view = events.project(row, officer_user, order)
        assert officer_view["type"] == "metrics_changed"
        assert not any(key in officer_view for key in ("payload", "actor", "phone", "name"))


def test_leader_overview_scoping_and_detail_endpoint(world):
    buyer, leader = world.login("+919000000002"), world.login("+919000000001")
    with db.S() as session:
        group = db.Group(name="Other SHG", village="Elsewhere")
        session.add(group)
        session.flush()
        session.add(db.User(phone="+919000000003", name="Other leader", role="leader", group_id=group.id))
        session.commit()
    other_group_leader = world.login("+919000000003")
    group_id = buyer.get("/api/buyer/groups").json()[0]["id"]
    order_id = buyer.post("/api/buyer/orders", json={
        "group_id": group_id, "text": fixtures.DATA["order_text"]
    }).json()["id"]
    overview = leader.get("/api/leader/overview")
    assert overview.status_code == 200
    data = overview.json()
    assert data["kpis"]["active_orders"] == 1
    assert data["decision_queue"][0]["kind"] == "submitted"
    assert data["orders"][0]["health"] == "Waiting"
    summary = leader.get("/api/leader/overview?summary=true").json()
    assert "orders" not in summary and summary["decision_count"] == 1
    assert leader.get(f"/api/leader/orders/{order_id}").status_code == 200
    assert other_group_leader.get(f"/api/leader/orders/{order_id}").status_code == 404
    assert buyer.get("/api/leader/overview").status_code == 403


def test_stream_requires_authenticated_session(world):
    from fastapi.testclient import TestClient
    import main

    assert TestClient(main.app).get("/api/stream").status_code == 401


def test_stream_replays_only_scoped_changes(world):
    import asyncio
    import auth
    from starlette.requests import Request
    import main

    buyer, leader = world.login("+919000000002"), world.login("+919000000001")
    group_id = buyer.get("/api/buyer/groups").json()[0]["id"]
    order_id = buyer.post("/api/buyer/orders", json={
        "group_id": group_id, "text": fixtures.DATA["order_text"]
    }).json()["id"]
    with db.S() as session:
        seq = session.query(db.LedgerEvent.i).filter_by(type="order.submitted", order_id=order_id).scalar()
        group = db.Group(name="Unrelated group")
        session.add(group)
        session.flush()
        outsider = db.User(phone="+919000000003", name="Out of scope", role="leader", group_id=group.id)
        session.add(outsider)
        session.commit()
        outsider_id = outsider.id

    async def consume(uid):
        async def receive():
            return {"type": "http.disconnect"}
        request = Request({
            "type": "http", "method": "GET", "path": "/api/stream",
            "query_string": f"since={seq - 1}".encode(),
            "headers": [(b"cookie", f"sk={auth.make_token(uid)}".encode())],
        }, receive)
        response = await main.stream(request)
        chunks = [await anext(response.body_iterator)]
        try:
            chunks.append(await anext(response.body_iterator))
        except StopAsyncIteration:
            pass
        finally:
            await response.body_iterator.aclose()
        return response.headers, "".join(chunks)

    leader_headers, leader_body = asyncio.run(consume(leader.get("/api/me").json()["id"]))
    outsider_headers, outsider_body = asyncio.run(consume(outsider_id))
    assert leader_headers["content-type"].startswith("text/event-stream")
    assert leader_headers["x-accel-buffering"] == "no"
    assert f"id: {seq}" in leader_body and f"Order #{order_id}" in leader_body
    assert f"id: {seq}" not in outsider_body


def test_member_home_and_leader_team_share_dashboard_and_whatsapp_updates(world):
    import bot

    buyer, leader, member = (
        world.login("+919000000002"),
        world.login("+919000000001"),
        world.login("+919000000100"),
    )
    group_id = buyer.get("/api/buyer/groups").json()[0]["id"]
    order_id = buyer.post("/api/buyer/orders", json={
        "group_id": group_id, "text": fixtures.DATA["order_text"]
    }).json()["id"]
    leader.post(f"/api/leader/orders/{order_id}/quote", json={})
    buyer.post(f"/api/buyer/orders/{order_id}/accept")
    leader.post(f"/api/leader/orders/{order_id}/plan")
    leader.post(f"/api/leader/orders/{order_id}/start")

    message = bot.handle("whatsapp:+919000000100", f"DONE {order_id} 3")
    assert "Recorded" in message
    with db.S() as session:
        assert session.query(db.User).filter_by(phone="+919000000100").one().last_active_at is not None
    assert member.post(f"/api/seller/orders/{order_id}/done", json={"n": 2, "stage": "packing"}).status_code == 403
    member.post(f"/api/seller/orders/{order_id}/done", json={"n": 2, "stage": "cutting"})

    home = member.get("/api/seller/home")
    assert home.status_code == 200
    body = home.json()
    task = next(item for item in body["tasks"] if item["order_id"] == order_id)
    assert task["done"] == 5
    assert body["pieces_this_week"] == 5
    assert "pay" not in task and task["name"] == "Shobha"
    assert all(entry["line"]["member"] == member.get("/api/me").json()["id"] for entry in body["earnings"])
    with db.S() as session:
        assert session.get(db.User, member.get("/api/me").json()["id"]).last_active_at is not None

    team = leader.get("/api/leader/team")
    assert team.status_code == 200
    worker = next(item for item in team.json() if item["name"] == "Shobha")
    assert any(item["order_id"] == order_id and item["done"] == 5 for item in worker["tasks"])
    overview = leader.get("/api/leader/overview").json()
    assert next(stage for stage in overview["orders"][0]["stages"] if stage["name"] == "cutting")["done"] == 5
    assert all("pay" not in item and "earnings" not in item for item in team.json())
    assert member.get("/api/leader/team").status_code == 403

    with db.S() as session:
        rows = session.query(db.LedgerEvent).filter_by(order_id=order_id, type="progress.reported").order_by(db.LedgerEvent.i).all()
        order = session.get(db.Order, order_id)
        member_user = member.get("/api/me").json()
        projected = [events.project(row, member_user, order) for row in rows]
        assert len(projected) == 2
        assert all(event and event["refresh"] == ["tasks", "earnings", "notices"] for event in projected)
        assert all("payload" not in event and "actor" not in event for event in projected)
