"""Geração de PDF bancário com despacho por banco."""
from __future__ import annotations

import re

from pycobranca.bancos import Bancos
from pycobranca.exceptions import PyCobrancaError
from pycobranca.render import emite_boleto, logo_do_banco

from apps.core.services.exceptions import DomainError
from apps.financeiro.services.boleto_bradesco import (
    dados_bancarios_bradesco,
    gerar_pdf_boleto as gerar_pdf_bradesco,
)


def _digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _endereco(objeto) -> str:
    return " ".join(filter(None, [
        getattr(objeto, "endereco", ""), getattr(objeto, "numero", ""),
        getattr(objeto, "complemento", ""), getattr(objeto, "bairro", ""),
        getattr(objeto, "cidade", ""), getattr(objeto, "uf", ""),
        getattr(objeto, "cep", ""),
    ]))


def _codigo_banco(boleto) -> str:
    return _digitos(boleto.conta_bancaria.banco_codigo).zfill(3)[-3:]


def _montar_boleto_multibanco(boleto, *, incluir_logo=True):
    conta = boleto.conta_bancaria
    titulo = boleto.conta_receber
    cliente = titulo.cliente
    filial = boleto.filial
    codigo = _codigo_banco(boleto)
    try:
        banco = Bancos.find(codigo)
        return banco(
            valor=boleto.valor,
            cedente=filial.razao_social,
            cedente_documento=filial.cnpj,
            cedente_endereco=_endereco(filial),
            agencia=conta.agencia,
            conta=conta.conta,
            carteira=boleto.carteira or conta.carteira_cobranca,
            convenio=conta.convenio or conta.codigo_transmissao,
            nosso_numero=boleto.nosso_numero,
            data_vencimento=boleto.data_vencimento,
            data_documento=boleto.data_emissao,
            numero_documento=boleto.seu_numero or titulo.documento_numero or str(titulo.pk),
            sacado=cliente.razao_social,
            sacado_documento=cliente.cpf_cnpj,
            sacado_endereco=_endereco(cliente),
            instrucoes=[linha for linha in (boleto.instrucoes or "").splitlines() if linha.strip()],
            digito_agencia=conta.agencia_digito,
            digito_conta=conta.conta_digito,
            digito_convenio=conta.digito_convenio,
            variacao=conta.variacao_carteira,
            posto=conta.posto_cobranca,
            byte_idt=conta.byte_identificacao,
            portfolio=conta.portfolio_cobranca,
            incremento=conta.incremento_cobranca,
            numero_contrato=conta.numero_contrato_cobranca,
            logo=logo_do_banco(codigo) if incluir_logo else None,
        )
    except PyCobrancaError as exc:
        raise DomainError(str(exc)) from exc


def validar_pdf_boleto(boleto) -> tuple[bool, str]:
    """Confere se os dados existentes bastam para montar um boleto pagável."""
    if not boleto:
        return False, "Boleto ainda não gerado."
    try:
        if _codigo_banco(boleto) == "237":
            dados_bancarios_bradesco(
                conta=boleto.conta_bancaria,
                nosso_numero=boleto.nosso_numero,
                vencimento=boleto.data_vencimento,
                valor=boleto.valor,
            )
        else:
            multibanco = _montar_boleto_multibanco(boleto, incluir_logo=False)
            multibanco.validar()
            _ = multibanco.codigo_barras
    except (DomainError, PyCobrancaError) as exc:
        return False, str(exc)
    return True, ""


def gerar_pdf_boleto(boleto) -> bytes:
    """Gera o PDF pelo motor homologado existente ou pelo motor multibanco."""
    if _codigo_banco(boleto) == "237":
        return gerar_pdf_bradesco(boleto)
    try:
        return emite_boleto(_montar_boleto_multibanco(boleto), modelo="classico").pdf
    except PyCobrancaError as exc:
        raise DomainError(str(exc)) from exc
