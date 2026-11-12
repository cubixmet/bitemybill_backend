"""Default two-level expense categories seeded per restaurant."""

DEFAULT_EXPENSE_TREE = [
    (
        "Rent",
        [
            "Shop / premises rent",
            "Equipment lease",
            "Storage / warehouse",
        ],
    ),
    (
        "Tax",
        [
            "GST / VAT",
            "Property tax",
            "Professional tax",
            "Other statutory",
        ],
    ),
    (
        "Event",
        [
            "Catering / banquet",
            "Decoration",
            "Entertainment",
            "Sponsorship",
        ],
    ),
    (
        "Utilities",
        [
            "Electricity",
            "Water",
            "Gas",
            "Internet / phone",
        ],
    ),
    (
        "Operations",
        [
            "Maintenance",
            "Cleaning",
            "Transportation",
            "Packaging",
        ],
    ),
    (
        "People",
        [
            "Salary",
            "Daily wage",
            "Staff welfare",
        ],
    ),
    (
        "Marketing",
        [
            "Ads & promotions",
            "Print & signage",
            "Loyalty / offers",
        ],
    ),
    (
        "Equipment",
        [
            "Kitchen equipment",
            "Furniture",
            "Repairs & spare parts",
        ],
    ),
    (
        "Miscellaneous",
        [
            "Stationery",
            "Petty cash",
            "Other",
        ],
    ),
]


def ensure_default_expense_categories(restaurant) -> list:
    from apps.expenses.models import ExpenseCategory

    created = []
    for order, (parent_name, children) in enumerate(DEFAULT_EXPENSE_TREE):
        parent, was_created = ExpenseCategory.objects.get_or_create(
            restaurant=restaurant,
            parent=None,
            name=parent_name,
            defaults={"sort_order": order, "is_active": True},
        )
        if was_created:
            created.append(parent)
        for child_order, child_name in enumerate(children):
            child, child_created = ExpenseCategory.objects.get_or_create(
                restaurant=restaurant,
                parent=parent,
                name=child_name,
                defaults={"sort_order": child_order, "is_active": True},
            )
            if child_created:
                created.append(child)
    return created
