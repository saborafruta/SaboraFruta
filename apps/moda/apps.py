"""
Registro de auditoria do vertical.

Cada modelo listado aqui passa a gravar em `LogSistema` a cada gravação e
exclusão: quem, quando, e o antes/depois campo a campo. A infraestrutura já
existia (`core.signals.register_for_audit`) e é a mesma que audita clientes,
compras e financeiro — o vertical só se inscreve nela.

A LISTA É DELIBERADA, não "todos os modelos". Ficam de fora os cadastros de
apoio (cor, tecido, tamanho) e os catálogos (operação, mockup): eles mudam
uma vez por ano, e auditá-los encheria a linha do tempo do pedido com ruído
que ninguém procura. Entra tudo que conta a história de um pedido — do
lançamento à entrega.

Custo: `pre_save` faz uma consulta a mais por gravação, para ter o valor
anterior. É o preço de saber o que mudou, e o volume aqui é de apontamento
de fábrica, não de PDV.
"""
from django.apps import AppConfig


class ModaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.moda"
    verbose_name = "Moda e Confecção"

    def ready(self):
        from apps.core.signals import register_for_audit

        from .models import (
            AprovacaoPedido, Encaixe, Expedicao, EtapaOrdem, FichaTecnica, Inspecao,
            ItemConferencia, ItemCorte, ItemGradePedido, ItemInspecao,
            ItemPedidoProducao, MaterialFicha, OperacaoRoteiro, OrdemProducao,
            PedidoProducao, Personalizacao, PersonalizacaoIndividual,
            RegistroCorte, RequisicaoMaterial, ReservaMaterial, Roteiro,
            VisualItemPedido, Volume,
        )

        for modelo in (
            # Comercial
            PedidoProducao, ItemPedidoProducao, ItemGradePedido,
            AprovacaoPedido,
            Personalizacao, VisualItemPedido, PersonalizacaoIndividual,
            # Engenharia e PCP
            FichaTecnica, MaterialFicha, Roteiro, OperacaoRoteiro,
            OrdemProducao, ReservaMaterial, RequisicaoMaterial,
            # Chão de fábrica
            EtapaOrdem, RegistroCorte, ItemCorte, Encaixe,
            Inspecao, ItemInspecao,
            # Expedição
            Expedicao, ItemConferencia, Volume,
        ):
            register_for_audit(modelo, 'moda')
