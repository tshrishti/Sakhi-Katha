from decimal import Decimal as D
import engine as E
import fixtures

def test_price_floor_and_underpay():
    p = E.price_check(200, 38, 40, 50, 8, 10, 60)
    assert p["per_unit"]["floor"] == "84.74" and p["loss_making"]
    assert p["effective_hourly_at_offer"] == "33.00"

def test_allocation_conserves_units_and_flags_overload():
    d = fixtures.DATA
    a = E.allocate(200, d["stages"], d["members"])
    for st in d["stages"]:
        assert sum(x["units"] for x in a["assignments"] if x["stage"] == st["name"]) == 200
    b = E.allocate(200, d["stages"], d["members"], declined=["m3"])
    assert all(x["member"] != "m3" for x in b["assignments"])

def test_payout_exact():
    r = E.payout(fixtures.DATA["payout_lines"])
    assert r["lines"][0]["paid"] == "1536.00" and r["lines"][0]["total_expected"] == "1600.00"
    assert D(r["total"]) == D("1600") + D("1000") + D("800") + D("800")

def test_ledger_detects_tampering():
    L = E.Ledger()
    for i in range(3): L.append("e", {"n": i}, "leader")
    assert L.verify()["valid"]
    L.events[1]["payload"]["n"] = 99
    assert L.verify() == {"valid": False, "broken_at": 1}

def test_parse_flags_unclear():
    r = E.parse_order(fixtures.DATA["order_text"])
    assert r["order"]["qty"] == 200 and r["order"]["offered_price"] == 60 and r["order"]["deadline_days"] == 5
    assert any("same as" in u for u in r["unclear"])
