from django.urls import path
from . import views

app_name = "finance"

urlpatterns = [
    path("overview/", views.financial_overview_view, name="overview"),
    path("expenses/<uuid:expense_id>/receipt/", views.download_expense_receipt, name="expense-receipt"),
    path("payments/<uuid:payment_id>/attachment/", views.download_payment_attachment, name="payment-attachment"),
]
