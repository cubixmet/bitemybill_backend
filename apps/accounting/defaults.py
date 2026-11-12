"""Standard restaurant chart of accounts (Indian SME style)."""

# code, name, type, gst_applicable
DEFAULT_CHART_OF_ACCOUNTS = [
    ("1000", "Current Assets", "asset", False),
    ("1010", "Cash on Hand", "asset", False),
    ("1020", "Bank Account", "asset", False),
    ("1100", "Accounts Receivable", "asset", False),
    ("1200", "Inventory / Stock", "asset", False),
    ("1300", "Input GST Credit", "asset", True),
    ("2000", "Current Liabilities", "liability", False),
    ("2100", "Accounts Payable", "liability", False),
    ("2200", "GST Payable", "liability", True),
    ("2210", "CGST Payable", "liability", True),
    ("2220", "SGST Payable", "liability", True),
    ("2230", "IGST Payable", "liability", True),
    ("2300", "Staff Payables", "liability", False),
    ("3000", "Equity", "equity", False),
    ("3100", "Owner Capital", "equity", False),
    ("3200", "Retained Earnings", "equity", False),
    ("4000", "Income", "income", False),
    ("4100", "Food Sales", "income", True),
    ("4200", "Beverage Sales", "income", True),
    ("4300", "Delivery / Service Income", "income", True),
    ("4400", "Other Income", "income", False),
    ("5000", "Cost of Goods Sold", "expense", False),
    ("5100", "Food Cost", "expense", False),
    ("5200", "Beverage Cost", "expense", False),
    ("6000", "Operating Expenses", "expense", False),
    ("6100", "Rent Expense", "expense", False),
    ("6200", "Utilities Expense", "expense", False),
    ("6300", "Payroll Expense", "expense", False),
    ("6400", "Marketing Expense", "expense", False),
    ("6500", "Maintenance Expense", "expense", False),
    ("6600", "Tax / Compliance Expense", "expense", False),
    ("6700", "Event Expense", "expense", False),
    ("6900", "Miscellaneous Expense", "expense", False),
]


def ensure_chart_of_accounts(restaurant) -> list:
    """Idempotent COA seed — one existence query + bulk_create for gaps (no N get_or_create)."""
    from apps.accounting.models import Account

    codes = [code for code, *_ in DEFAULT_CHART_OF_ACCOUNTS]
    existing = set(
        Account.objects.filter(restaurant=restaurant, code__in=codes).values_list("code", flat=True)
    )
    to_create = [
        Account(
            restaurant=restaurant,
            code=code,
            name=name,
            account_type=account_type,
            is_system=True,
            is_active=True,
            gst_applicable=gst,
        )
        for code, name, account_type, gst in DEFAULT_CHART_OF_ACCOUNTS
        if code not in existing
    ]
    if to_create:
        Account.objects.bulk_create(to_create)
    return to_create
