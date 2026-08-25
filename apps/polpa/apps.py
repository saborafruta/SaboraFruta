"""
Registro do vertical Polpa de Frutas.

A auditoria segue a mesma infraestrutura do resto do ERP
(`core.signals.register_for_audit`): cada gravacao grava em `LogSistema`
quem mexeu, quando e o antes/depois campo a campo.

A LISTA E' DELIBERADA. O recebimento entra porque e' o registro que decide
quanto se paga ao produtor e com que custo a fruta entra -- exatamente o
que alguem vai querer conferir seis meses depois. A ficha da fruta entra
porque e' a regua que aprova ou recusa carga: mudar o Brix minimo em
silencio muda o que a fabrica aceita. E a ficha do produto entra porque
mudar a validade em dias muda o vencimento de tudo que for produzido dali
para a frente.
"""
from django.apps import AppConfig


class PolpaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.polpa'
    verbose_name = 'Polpa de Frutas'

    def ready(self):
        from apps.core.signals import register_for_audit

        from .models import (
            ApontamentoEtapa, Camara, EtapaReceita, FichaProduto, Fruta,
            LoteArmazenado, OrdemPolpa, Receita, Recebimento, Recurso,
        )

        for modelo in (
            ApontamentoEtapa, Camara, EtapaReceita, FichaProduto, Fruta,
            LoteArmazenado, OrdemPolpa, Receita, Recebimento, Recurso,
        ):
            register_for_audit(modelo, modulo='polpa')
