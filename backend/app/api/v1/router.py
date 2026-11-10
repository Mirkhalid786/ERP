"""API v1 router — aggregates all route modules."""
from fastapi import APIRouter

from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.auth import router as auth_router
from app.api.v1.routes.users import router as users_router
from app.api.v1.routes.tax_rates import router as tax_rates_router
from app.api.v1.routes.categories import router as categories_router
from app.api.v1.routes.manufacturers import router as manufacturers_router
from app.api.v1.routes.units import router as units_router
from app.api.v1.routes.catalogue import router as catalogue_router
from app.api.v1.routes.bill_imports import router as bill_imports_router
from app.api.v1.routes.medicines import router as medicines_router
from app.api.v1.routes.batches import router as batches_router
from app.api.v1.routes.inventory import router as inventory_router
from app.api.v1.routes.suppliers import router as suppliers_router
from app.api.v1.routes.purchase_orders import router as purchase_orders_router
from app.api.v1.routes.goods_receipts import router as goods_receipts_router
from app.api.v1.routes.purchase_invoices import router as purchase_invoices_router
from app.api.v1.routes.direct_purchases import router as direct_purchases_router
from app.api.v1.routes.purchase_returns import router as purchase_returns_router
from app.api.v1.routes.pos import router as pos_router
from app.api.v1.routes.customers import router as customers_router
from app.api.v1.routes.sales import router as sales_router
from app.api.v1.routes.sales_ops import router as sales_ops_router
from app.api.v1.routes.sales_reports import router as sales_reports_router
from app.api.v1.routes.messaging import router as messaging_router
from app.api.v1.routes.accounting import router as accounting_router
from app.api.v1.routes.payables import router as payables_router
from app.api.v1.routes.admin import router as admin_router
from app.api.v1.routes.branding import router as branding_router

api_router = APIRouter()

api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(tax_rates_router)
api_router.include_router(categories_router)
api_router.include_router(manufacturers_router)
api_router.include_router(units_router)
api_router.include_router(catalogue_router)
api_router.include_router(bill_imports_router)
api_router.include_router(medicines_router)
api_router.include_router(batches_router)
api_router.include_router(inventory_router)
api_router.include_router(suppliers_router)
api_router.include_router(purchase_orders_router)
api_router.include_router(goods_receipts_router)
api_router.include_router(purchase_invoices_router)
api_router.include_router(direct_purchases_router)
api_router.include_router(purchase_returns_router)
api_router.include_router(pos_router)
api_router.include_router(customers_router)
api_router.include_router(sales_ops_router)   # /sales/returns before /sales/invoices/{id}
api_router.include_router(sales_router)
api_router.include_router(sales_reports_router)
api_router.include_router(messaging_router)
api_router.include_router(accounting_router)
api_router.include_router(payables_router)
api_router.include_router(admin_router)
api_router.include_router(branding_router)
