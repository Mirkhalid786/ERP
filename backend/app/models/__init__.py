"""Models package — import all models so Alembic autogenerate detects them."""
from app.models.auth import (  # noqa: F401
    Pharmacy,
    Role,
    Permission,
    RolePermission,
    User,
    UserSession,
    Setting,
    AuditLog,
)
from app.models.inventory import (  # noqa: F401
    TaxRate,
    Category,
    Manufacturer,
    Unit,
    Medicine,
    MedicineBarcode,
    MedicineUnitConversion,
    MedicineBatch,
    StockMovement,
    StockAdjustment,
    StockAdjustmentItem,
)
from app.models.numbering import DocumentSequence  # noqa: F401
from app.models.purchasing import (  # noqa: F401
    Supplier,
    PurchaseOrder,
    PurchaseOrderItem,
    GoodsReceipt,
    GoodsReceiptItem,
    SupplierInvoice,
    SupplierInvoiceItem,
    SupplierReturn,
    SupplierReturnItem,
)
from app.models.sales import (  # noqa: F401
    Customer,
    CashShift,
    CashMovement,
    SalesInvoice,
    SalesInvoiceItem,
    SalePrescription,
    CustomerPayment,
    PaymentAllocation,
    CustomerRefund,
    CustomerLedgerEntry,
    SalesReturn,
    SalesReturnItem,
    MessageOutbox,
)
from app.models.accounting import (  # noqa: F401
    LedgerAccount,
    FiscalYear,
    AccountingPeriod,
    JournalEntry,
    JournalLine,
    OpeningBalanceBatch,
    OpeningBalanceLine,
    GlPostingIssue,
    PaymentSettlement,
    BankStatementLine,
)
from app.models.payables import (  # noqa: F401
    SupplierPayment,
    SupplierPaymentAllocation,
    ExpenseCategory,
    Expense,
    SupplierCreditNote,
)
from app.models.admin import BackupRecord  # noqa: F401
from app.models.branding import BrandAsset, UserPreference  # noqa: F401
from app.models.bill_import import PurchaseBillImport  # noqa: F401
