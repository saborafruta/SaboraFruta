from django.apps import AppConfig


class ComprasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.compras'
    verbose_name = 'Compras'

    def ready(self):
        from apps.core.signals import register_for_audit
        from .models import CotacaoCompra, EntradaNF, EntradaNFAjusteFinanceiro, EntradaNFParcela, EntradaNFRateioFinanceiro, PedidoCompra
        register_for_audit(PedidoCompra, 'compras')
        register_for_audit(EntradaNF, 'compras')
        register_for_audit(EntradaNFParcela, 'compras')
        register_for_audit(EntradaNFAjusteFinanceiro, 'financeiro')
        register_for_audit(EntradaNFRateioFinanceiro, 'financeiro')
        register_for_audit(CotacaoCompra, 'compras')
