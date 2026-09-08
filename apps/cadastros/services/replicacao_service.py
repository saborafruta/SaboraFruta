"""Replica cadastros seguros entre filiais da mesma empresa."""
from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist

from apps.core.tenant_context import tenant_atomic
from apps.cadastros.models import Cliente, ClienteFilial, Fornecedor, FornecedorFilial
from apps.core.models import Filial


CLIENTE_CAMPOS_REPLICAVEIS = [
    'tipo_pessoa', 'tipo', 'razao_social', 'nome_fantasia', 'cpf_cnpj', 'rg_ie',
    'inscricao_municipal', 'inscricao_estadual', 'data_nascimento', 'sexo',
    'endereco', 'numero', 'complemento', 'bairro', 'cidade', 'uf', 'cep',
    'codigo_municipio_ibge', 'pais', 'codigo_pais_bacen', 'id_estrangeiro',
    'telefone', 'celular', 'email', 'email_nfe', 'contato_nome',
    'prazo_pagamento_dias', 'grupo_desconto', 'consumidor_final',
    'contribuinte_icms', 'optante_simples', 'id_externo', 'observacao', 'ativo',
]

FORNECEDOR_CAMPOS_REPLICAVEIS = [
    'tipo_pessoa', 'razao_social', 'nome_fantasia', 'cpf_cnpj',
    'inscricao_estadual', 'inscricao_municipal', 'endereco', 'numero',
    'complemento', 'bairro', 'cidade', 'uf', 'cep', 'codigo_municipio_ibge',
    'pais', 'codigo_pais_bacen', 'telefone', 'celular', 'email',
    'contato_nome', 'prazo_entrega_dias', 'contribuinte_icms',
    'optante_simples', 'id_externo', 'observacao', 'ativo',
]


def _politica(filial):
    if not filial or not filial.empresa_id:
        return None
    try:
        politica = filial.politica_replicacao
    except ObjectDoesNotExist:
        try:
            politica = filial.empresa.politica_replicacao
        except ObjectDoesNotExist:
            return None
    if not getattr(politica, 'ativo', True):
        return None
    return politica


def _filiais_destino(filial, campo_politica=None):
    filiais = Filial.objects.filter(
        empresa_id=filial.empresa_id,
        ativo=True,
        participa_replicacao=True,
    ).exclude(pk=filial.pk)
    if not campo_politica:
        return filiais
    filiais_permitidas = [
        destino.pk
        for destino in filiais
        if getattr(_politica(destino), campo_politica, False)
    ]
    return filiais.filter(pk__in=filiais_permitidas)


def _vincular(vinculo_modelo, campo_objeto, objeto, filial):
    vinculo_modelo.objects.update_or_create(
        **{campo_objeto: objeto, 'filial': filial},
        defaults={'ativo': True},
    )


def _sincronizar(modelo, origem, campos):
    vinculo_modelo = ClienteFilial if modelo is Cliente else FornecedorFilial
    campo_objeto = 'cliente' if modelo is Cliente else 'fornecedor'
    _vincular(vinculo_modelo, campo_objeto, origem, origem.filial)
    campo_politica = 'replicar_clientes' if modelo is Cliente else 'replicar_fornecedores'
    for filial in _filiais_destino(origem.filial, campo_politica):
        _vincular(vinculo_modelo, campo_objeto, origem, filial)


class ReplicacaoCadastrosService:
    @staticmethod
    @tenant_atomic
    def sincronizar_cliente(cliente):
        politica = _politica(cliente.filial)
        if (
            not politica
            or not politica.replicar_clientes
            or not getattr(cliente.filial, 'participa_replicacao', True)
        ):
            _vincular(ClienteFilial, 'cliente', cliente, cliente.filial)
            return
        _sincronizar(Cliente, cliente, CLIENTE_CAMPOS_REPLICAVEIS)

    @staticmethod
    @tenant_atomic
    def sincronizar_fornecedor(fornecedor):
        politica = _politica(fornecedor.filial)
        if (
            not politica
            or not politica.replicar_fornecedores
            or not getattr(fornecedor.filial, 'participa_replicacao', True)
        ):
            _vincular(FornecedorFilial, 'fornecedor', fornecedor, fornecedor.filial)
            return
        _sincronizar(Fornecedor, fornecedor, FORNECEDOR_CAMPOS_REPLICAVEIS)

    @staticmethod
    @tenant_atomic
    def sincronizar_clientes_da_filial(filial):
        politica = _politica(filial)
        if (
            not politica
            or not politica.replicar_clientes
            or not getattr(filial, 'participa_replicacao', True)
        ):
            return 0
        total = 0
        for cliente in Cliente.objects.filter(filial=filial).iterator():
            _sincronizar(Cliente, cliente, CLIENTE_CAMPOS_REPLICAVEIS)
            total += 1
        return total

    @staticmethod
    @tenant_atomic
    def sincronizar_fornecedores_da_filial(filial):
        politica = _politica(filial)
        if (
            not politica
            or not politica.replicar_fornecedores
            or not getattr(filial, 'participa_replicacao', True)
        ):
            return 0
        total = 0
        for fornecedor in Fornecedor.objects.filter(filial=filial).iterator():
            _sincronizar(Fornecedor, fornecedor, FORNECEDOR_CAMPOS_REPLICAVEIS)
            total += 1
        return total
