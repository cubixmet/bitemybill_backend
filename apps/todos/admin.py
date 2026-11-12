from django.contrib import admin

from apps.todos.models import Todo


@admin.register(Todo)
class TodoAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "priority", "due_at", "remind_at", "alarm_enabled", "restaurant")
    list_filter = ("status", "priority", "alarm_enabled")
    search_fields = ("title", "notes")
