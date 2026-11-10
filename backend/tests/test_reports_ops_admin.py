"""Stage 5C statements, Stage 5D operations, and Phase 6/7 administration."""
import io
import zipfile

import pytest
from httpx import AsyncClient

from tests.conftest import login
from tests.sales_helpers import D, add_user, create_customer, key, line, login_as, receive_stock, sell
from tests.test_gl_posting import TODAY, balances, bill_for, books


async def balances_at(client, on) -> dict:
    tb = (await client.get(f"/api/v1/accounting/trial-balance?date_to={on}")).json()
    assert tb["is_balanced"], tb["totals"]
    return {r["code"]: D(r["closing_debit"]) - D(r["closing_credit"]) for r in tb["rows"]}


async def cycle(client) -> dict:
    """Books, stock, a cash sale, a UPI sale, a credit sale, an expense and a supplier bill + part payment."""
    data = await books(client)
    batch = await receive_stock(client, data, "RPT-B1")
    await sell(client, [line(data, "2")], [{"method": "CASH", "amount": "84.00"}])
    await sell(client, [line(data, "1")], [{"method": "UPI", "amount": "42.00", "reference": "U-1"}])
    cust = await create_customer(client)
    await sell(client, [line(data, "1")], [], customer_id=cust["id"])
    cats = {c["code"]: c for c in (await client.get("/api/v1/expenses/categories")).json()}
    await client.post("/api/v1/expenses", json={"idempotency_key": key(), "category_id": cats["RENT"]["id"], "expense_date": str(TODAY),
                                                "amount": "100.00", "payee": "Landlord", "payment_method": "CASH",
                                                "cash_source": "OFFICE_CASH"})
    await bill_for(client, data, batch)
    pay = await client.post("/api/v1/supplier-payments", json={
        "idempotency_key": key(), "supplier_id": data["supplier"]["id"], "payment_date": str(TODAY),
        "method": "BANK_TRANSFER", "reference": "UTR-RPT", "amount": "136.00", "auto_allocate": True})
    assert pay.status_code == 201, pay.text
    return {**data, "customer": cust, "bank": next(a for a in (await client.get("/api/v1/accounting/accounts")).json()
                                                   if a["system_key"] == "BANK")}


@pytest.mark.asyncio
async def test_financial_statements_reconcile_to_the_ledger(client: AsyncClient):
    await cycle(client)
    q = f"?date_from={TODAY.replace(day=1)}&date_to={TODAY}"
    pl = (await client.get(f"/api/v1/accounting/reports/profit-loss{q}")).json()
    b = await balances(client)
    revenue = -b["4100"]
    assert D(pl["totals"]["revenue"]) == revenue == D("150.00")             # 4 strips x 37.50 taxable
    assert D(pl["totals"]["cost_of_sales"]) == b["5100"] == D("120.00")
    assert D(pl["totals"]["expenses"]) == D("100.00")
    assert D(pl["net_profit"]) == D("-70.00") and D(pl["gross_profit"]) == D("30.00")
    bs = (await client.get(f"/api/v1/accounting/reports/balance-sheet?date_to={TODAY}")).json()
    assert bs["is_balanced"], bs["totals"]
    assert any(r["name"].startswith("Profit / (loss)") and D(r["amount"]) == D("-70.00") for r in bs["sides"]["equity"])
    cf = (await client.get(f"/api/v1/accounting/reports/cash-flow{q}")).json()
    assert cf["reconciles"] and D(cf["closing_cash"]) == b["1110"] + b["1121"] + b["1131"]
    gst = (await client.get(f"/api/v1/accounting/reports/gst{q}")).json()
    assert D(gst["output_total"]) == D("18.00") and D(gst["input_pending_review"]) == D("36.00")
    ageing = (await client.get(f"/api/v1/accounting/reports/payables-ageing?date_to={TODAY}")).json()
    assert D(ageing["total"]) == D("200.00") and D(ageing["totals"]["current"]) == D("200.00")
    book = (await client.get(f"/api/v1/accounting/reports/day-book{q}")).json()
    assert book["total"] >= 7 and all(sum(D(l["debit"]) for l in e["lines"]) == sum(D(l["credit"]) for l in e["lines"])
                                      for e in book["entries"])
    cash_book = (await client.get(f"/api/v1/accounting/reports/cash-book{q}")).json()
    assert D(cash_book["closing_balance"]) == b["1110"]
    csv = await client.get(f"/api/v1/accounting/reports/profit-loss{q}&format=csv")
    assert csv.status_code == 200 and "section" in csv.text
    await add_user(client, "Pharmacist", "pharma1")
    await login_as(client, "pharma1", "Staff1234")
    assert (await client.get(f"/api/v1/accounting/reports/profit-loss{q}")).status_code == 403


@pytest.mark.asyncio
async def test_settlement_moves_clearing_to_bank_net_of_fees(client: AsyncClient):
    data = await cycle(client)
    body = {"idempotency_key": key(), "clearing_key": "UPI_CLEARING", "bank_account_id": data["bank"]["id"],
            "settlement_date": str(TODAY), "gross_amount": "42.00", "fee_amount": "1.00", "fee_gst_amount": "0.18",
            "reference": "UPI-SETTLE-1"}
    too_much = await client.post("/api/v1/accounting/settlements", json={**body, "idempotency_key": key(), "gross_amount": "50.00"})
    assert too_much.status_code == 422 and "waiting" in too_much.json()["detail"]
    st = await client.post("/api/v1/accounting/settlements", json=body)
    assert st.status_code == 201, st.text
    assert D(st.json()["net_amount"]) == D("40.82")
    replay = await client.post("/api/v1/accounting/settlements", json=body)
    assert replay.status_code == 200 and replay.json()["id"] == st.json()["id"]
    b = await balances(client)
    assert b.get("1131", 0) == 0 and b["5280"] == D("1.00") and b["1121"] == D("40.82") - D("136.00")
    rev = await client.post(f"/api/v1/accounting/settlements/{st.json()['id']}/reverse", json={"reason": "Bank returned the credit"})
    assert rev.status_code == 200, rev.text
    b = await balances(client)
    assert b["1131"] == D("42.00") and b.get("5280", 0) == 0
    r = (await client.get("/api/v1/accounting/postings/reconciliation")).json()
    assert r["ok"], r


@pytest.mark.asyncio
async def test_bank_statement_import_match_and_report(client: AsyncClient):
    data = await cycle(client)
    bank_id = data["bank"]["id"]
    rows = [{"statement_date": str(TODAY), "description": "NEFT to supplier", "reference": "UTR-RPT", "amount": "-136.00"},
            {"statement_date": str(TODAY), "description": "Bank charges", "amount": "-5.00"}]
    imp = await client.post("/api/v1/accounting/bank/statements", json={"account_id": bank_id, "rows": rows})
    assert imp.status_code == 200, imp.text
    assert imp.json()["added"] == 2 and imp.json()["auto_matched"] == 1
    again = (await client.post("/api/v1/accounting/bank/statements", json={"account_id": bank_id, "rows": rows})).json()
    assert again["added"] == 0 and again["skipped_duplicates"] == 2
    rep = (await client.get(f"/api/v1/accounting/bank/{bank_id}/reconciliation")).json()
    assert rep["matched_lines"] == 1 and len(rep["unmatched_statement"]) == 1 and D(rep["book_balance"]) == D("-136.00")
    charge = rep["unmatched_statement"][0]
    some_line = (await client.get(f"/api/v1/accounting/ledger/{bank_id}")).json()["entries"][0]
    wrong = await client.post(f"/api/v1/accounting/bank/statement-lines/{charge['id']}/match", json={"journal_line_id": 999999})
    assert wrong.status_code == 422
    cash = next(a for a in (await client.get("/api/v1/accounting/accounts")).json() if a["system_key"] == "CASH")
    assert (await client.post("/api/v1/accounting/bank/statements", json={"account_id": cash["id"], "rows": rows})).status_code == 422
    assert some_line


@pytest.mark.asyncio
async def test_year_end_close_moves_profit_to_retained_earnings_and_reopens(client: AsyncClient):
    await cycle(client)
    fy = (await client.get("/api/v1/accounting/periods")).json()[0]
    blocked = await client.post(f"/api/v1/accounting/fiscal-years/{fy['id']}/close")
    assert blocked.status_code == 409 and "Close these periods" in blocked.json()["detail"]
    for p in fy["periods"][:-1]:
        if p["status"] == "OPEN":
            assert (await client.post(f"/api/v1/accounting/periods/{p['id']}/close")).status_code == 200
    closed = await client.post(f"/api/v1/accounting/fiscal-years/{fy['id']}/close")
    assert closed.status_code == 200, closed.text
    b = await balances_at(client, fy["end_date"])
    assert b["3300"] == D("70.00")                                      # Loss of 70 debited to retained earnings
    assert all(v == 0 for code, v in b.items() if code[0] in "45")      # Income and expense accounts zeroed
    pl = (await client.get(f"/api/v1/accounting/reports/profit-loss?date_from={fy['start_date']}&date_to={fy['end_date']}")).json()
    assert D(pl["net_profit"]) == D("-70.00")                           # The closed year still shows its result
    bs = (await client.get(f"/api/v1/accounting/reports/balance-sheet?date_to={fy['end_date']}")).json()
    assert bs["is_balanced"] and not any(r["name"].startswith("Profit / (loss)") for r in bs["sides"]["equity"])
    years = (await client.get("/api/v1/accounting/periods")).json()
    assert len(years) == 2 and years[0]["status"] == "CLOSED"
    assert (await client.post(f"/api/v1/accounting/fiscal-years/{fy['id']}/close")).status_code == 409
    reopened = await client.post(f"/api/v1/accounting/fiscal-years/{fy['id']}/reopen", json={"reason": "Late supplier bill to add"})
    assert reopened.status_code == 200, reopened.text
    b = await balances_at(client, fy["end_date"])
    assert b.get("3300", 0) == 0 and b["5210"] == D("100.00")
    again = await client.post(f"/api/v1/accounting/fiscal-years/{fy['id']}/close")
    assert again.status_code == 200, again.text                         # Can be closed again with a new closing entry


@pytest.mark.asyncio
async def test_ca_pack_and_purchase_report(client: AsyncClient):
    await cycle(client)
    resp = await client.get(f"/api/v1/accounting/export/ca-pack?date_from={TODAY.replace(day=1)}&date_to={TODAY}")
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())
    assert {"README.txt", "trial_balance.csv", "profit_and_loss.csv", "balance_sheet.csv", "journal_lines.csv",
            "gst_from_ledger.csv", "payables_ageing.csv", "receivables_ageing.csv", "sales_register.csv"} <= names
    pr = (await client.get(f"/api/v1/reports/purchases?date_from={TODAY.replace(day=1)}&date_to={TODAY}")).json()
    assert pr["totals"]["bills"] == 1 and D(pr["totals"]["total"]) == D("336.00") and D(pr["totals"]["paid"]) == D("136.00")
    assert any(D(r["tax"]) == D("36.00") for r in pr["input_gst_by_rate"])


@pytest.mark.asyncio
async def test_audit_viewer_health_and_permissions(client: AsyncClient):
    await cycle(client)
    logs = (await client.get("/api/v1/admin/audit-logs?action=expense")).json()
    assert logs["total"] >= 1 and all(i["action"].startswith("expense") for i in logs["items"])
    assert "sale.finalize" in logs["facets"]["actions"]
    health = (await client.get("/api/v1/admin/system-health")).json()
    assert {"database", "migrations", "disk", "backup", "worker", "messaging", "accounting"} <= set(health["checks"])
    assert health["checks"]["accounting"]["set_up"] is True and health["checks"]["backup"]["ok"] is False
    exp = (await client.get("/api/v1/inventory/alerts/near-expiry?days=365")).json()
    assert exp and D(exp[0]["stock_value"]) > 0
    await add_user(client, "Cashier", "cash1")
    await login_as(client, "cash1", "Staff1234")
    for url in ("/api/v1/admin/audit-logs", "/api/v1/admin/system-health", "/api/v1/admin/backups"):
        assert (await client.get(url)).status_code == 403, url
    assert (await client.post("/api/v1/admin/backups")).status_code == 403
    await login(client)


@pytest.mark.asyncio
async def test_general_settings_are_validated_and_audited(client: AsyncClient):
    from tests.test_purchasing import setup_pharmacy_and_catalogue
    await setup_pharmacy_and_catalogue(client)
    rows = {r["key"]: r for r in (await client.get("/api/v1/settings/general")).json()}
    assert "backup.daily_time" in rows and "accounting.books_start_date" not in rows and "messaging.template.invoice" not in rows
    bad = await client.put("/api/v1/settings/general/backup.daily_time", json={"setting_value": "25:99"})
    assert bad.status_code == 422
    ok = await client.put("/api/v1/settings/general/backup.daily_time", json={"setting_value": "22:30"})
    assert ok.status_code == 200 and ok.json()["value"] == "22:30"
    assert (await client.put("/api/v1/settings/general/pos.max_discount_percent", json={"setting_value": "150"})).status_code == 422
    assert (await client.put("/api/v1/settings/general/accounting.books_start_date", json={"setting_value": "2026-01-01"})).status_code == 404
    logs = (await client.get("/api/v1/admin/audit-logs?action=settings.update")).json()
    assert logs["total"] == 1


@pytest.mark.asyncio
async def test_invoice_numbers_restart_on_financial_year_rollover(client: AsyncClient, monkeypatch):
    """1 April starts a new GST series (INV/27-28/00001); 31 March still belongs to the old year."""
    from datetime import date
    import app.services.sales_common as sc
    from tests.sales_helpers import sales_setup
    data = await sales_setup(client)
    monkeypatch.setattr(sc, "today_local", lambda: date(2027, 3, 31))
    last_old = await sell(client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}])
    monkeypatch.setattr(sc, "today_local", lambda: date(2027, 4, 1))
    first_new = await sell(client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}])
    second_new = await sell(client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}])
    assert "/26-27/" in last_old["invoice_number"]
    assert first_new["invoice_number"].endswith("/27-28/00001") and second_new["invoice_number"].endswith("/27-28/00002")


@pytest.mark.asyncio
async def test_posting_status_distinguishes_posted_pending_failed(client: AsyncClient):
    data = await cycle(client)
    sale = await sell(client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}])
    draft = (await client.post("/api/v1/purchases/invoices", json={
        "supplier_id": data["supplier"]["id"], "invoice_number": "ST-DRAFT", "invoice_date": str(TODAY), "received_date": str(TODAY),
        "supplier_declared_total": "30.00", "items": [{"medicine_id": data["medicine"]["id"], "batch_number": "Q",
                                                       "packaging_unit_id": data["strip_unit"]["id"], "billed_qty_pack": "1.00",
                                                       "purchase_rate": "30.00"}]})).json()
    periods = (await client.get("/api/v1/accounting/periods")).json()[0]["periods"]
    current = next(p for p in periods if p["start_date"] <= str(TODAY) <= p["end_date"])
    await client.post(f"/api/v1/accounting/periods/{current['id']}/close")
    failed = await sell(client, [line(data, "1")], [{"method": "CASH", "amount": "42.00"}])
    keys = f"SALE:{sale['id']},SUPPLIER_BILL:{draft['id']},SALE:{failed['id']}"
    st = (await client.get(f"/api/v1/accounting/postings/status?keys={keys}")).json()
    assert st[f"SALE:{sale['id']}"]["status"] == "POSTED" and st[f"SALE:{sale['id']}"]["entry_number"]
    assert st[f"SUPPLIER_BILL:{draft['id']}"]["status"] == "NOT_APPLICABLE"
    assert st[f"SALE:{failed['id']}"]["status"] == "FAILED" and "closed" in st[f"SALE:{failed['id']}"]["error"]
