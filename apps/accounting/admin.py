from django.contrib import admin

from apps.accounting.models import Account, JournalEntry, JournalLine


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "restaurant", "is_active", "gst_applicable")
    list_filter = ("account_type", "is_active", "gst_applicable")
    search_fields = ("code", "name")


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("number", "date", "status", "memo", "restaurant", "reference")
    list_filter = ("status", "date")
    search_fields = ("number", "memo", "reference")
    inlines = [JournalLineInline]
