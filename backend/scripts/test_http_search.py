import asyncio
import httpx

async def test_api_searches():
    async with httpx.AsyncClient(base_url="http://localhost:8000") as client:
        # Try logging in with Admin123 or common passwords
        login_resp = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin123"})
        if login_resp.status_code != 200:
            print(f"Login failed: {login_resp.status_code} {login_resp.text}")
            return
        cookies = login_resp.cookies
        print(f"Logged in successfully as: {login_resp.json().get('name')} (@{login_resp.json().get('username')})")

        # Test search queries
        queries = ["Augmentin", "Azithral", "Amoxycillin", "625 Duo", "Glaxo", "Dolo 650"]
        for q in queries:
            resp = await client.get(f"/api/v1/medicines?limit=5&search={q}", cookies=cookies)
            print(f"\n========================================================")
            print(f"HTTP GET /api/v1/medicines?search={q} (Status: {resp.status_code})")
            print(f"========================================================")
            items = resp.json()
            print(f"Hits returned: {len(items)}")
            for item in items:
                disc_badge = " [DISCONTINUED]" if item.get('is_discontinued') else ""
                print(f"  * [{item.get('id')}] {item.get('name')}{disc_badge}")
                print(f"      Manufacturer: {item.get('manufacturer_name')}")
                print(f"      Generic/Salt: {item.get('generic_name')}")
                print(f"      Strength: {item.get('strength')} | Form: {item.get('dosage_form')}")
                print(f"      MRP: ₹{item.get('mrp')} | Pack: {item.get('pack_size_label')}")
                print(f"      Source: {item.get('source_name')} (ID: {item.get('source_id')})")

if __name__ == "__main__":
    asyncio.run(test_api_searches())
