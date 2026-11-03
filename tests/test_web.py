def test_web_pages_require_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/login"


def test_login_and_pages(client):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "admin123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    for path in [
        "/",
        "/products",
        "/boms",
        "/purchase",
        "/work-orders",
        "/sales",
        "/stock",
        "/reports",
        "/automation",
    ]:
        page = client.get(path)
        assert page.status_code == 200, f"{path} -> {page.status_code}"


def test_api_auth_and_reports(client, auth):
    products = client.get("/api/products", headers=auth)
    assert products.status_code == 200
    assert len(products.json()) > 0

    margins = client.get("/api/reports/project-margin", headers=auth)
    assert margins.status_code == 200
    assert any(row["project_no"] == "PRJ-2026-002" for row in margins.json())

    unauthorized = client.cookies.clear() or client.get("/api/products")
    assert unauthorized.status_code == 401


def test_api_boms_and_stock(client, auth):
    boms = client.get("/api/boms", headers=auth).json()
    assert boms and boms[0]["items"], "种子数据应包含组件 BOM"

    stock = client.get("/api/stock/balances", headers=auth).json()
    assert any(row["sku"] == "CELL-182" for row in stock)


def test_api_work_order_flow(client, auth):
    work_orders = client.get("/api/work-orders", headers=auth).json()
    issued = [wo for wo in work_orders if wo["status"] == "ISSUED"]
    assert issued, "种子数据里应有已领料工单"

    target = issued[0]
    response = client.post(
        f"/api/work-orders/{target['id']}/complete",
        headers=auth,
        json={
            "outputs": [
                {"product_id": 999999, "qty": 1},
            ],
            "labor_cost": 0,
            "overhead_cost": 0,
        },
    )
    # 产出必须是成品 SKU，这里故意用错 product_id 验证校验生效
    assert response.status_code in (400, 422)
