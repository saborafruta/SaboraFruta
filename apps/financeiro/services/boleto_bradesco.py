"""Cálculo e impressão de boletos Bradesco conforme o MPO CNAB 400P."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from django.conf import settings
from django.urls import reverse

from apps.core.services.exceptions import DomainError
from apps.core.services.tenant_public_link_service import TenantPublicLinkService
from apps.core.tenant_context import get_current_database_alias


def _digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def _valor(valor, tamanho=10) -> str:
    centavos = int((Decimal(valor or 0) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    texto = str(centavos)
    if len(texto) > tamanho:
        raise DomainError("O valor do boleto excede o limite do layout bancário.")
    return texto.zfill(tamanho)


def digito_nosso_numero(carteira: str, nosso_numero: str) -> str:
    base = _digitos(carteira).zfill(2)[-2:] + _digitos(nosso_numero).zfill(11)[-11:]
    soma = 0
    peso = 2
    for caractere in reversed(base):
        soma += int(caractere) * peso
        peso = 2 if peso == 7 else peso + 1
    resto = soma % 11
    if resto == 0:
        return "0"
    if resto == 1:
        return "P"
    return str(11 - resto)


def fator_vencimento(vencimento: date) -> str:
    if vencimento >= date(2025, 2, 22):
        fator = 1000 + (vencimento - date(2025, 2, 22)).days
    else:
        fator = (vencimento - date(1997, 10, 7)).days
    if fator < 0 or fator > 9999:
        raise DomainError("A data de vencimento está fora do intervalo aceito no boleto bancário.")
    return str(fator).zfill(4)


def digito_codigo_barras(codigo_sem_dv: str) -> str:
    soma = 0
    peso = 2
    for caractere in reversed(codigo_sem_dv):
        soma += int(caractere) * peso
        peso = 2 if peso == 9 else peso + 1
    resultado = 11 - (soma % 11)
    return "1" if resultado in (0, 1) or resultado > 9 else str(resultado)


def digito_modulo_10(campo: str) -> str:
    soma = 0
    peso = 2
    for caractere in reversed(campo):
        produto = int(caractere) * peso
        soma += (produto // 10) + (produto % 10)
        peso = 1 if peso == 2 else 2
    return str((10 - soma % 10) % 10)


def dados_bancarios_bradesco(*, conta, nosso_numero, vencimento, valor):
    if _digitos(conta.banco_codigo).zfill(3)[-3:] != "237":
        raise DomainError("O boleto deste manual só pode ser emitido para conta Bradesco (banco 237).")
    agencia = _digitos(conta.agencia)
    numero_conta = _digitos(conta.conta)
    carteira = _digitos(conta.carteira_cobranca)
    nosso = _digitos(nosso_numero)
    if not agencia or len(agencia) > 4:
        raise DomainError("No Bradesco, informe a agência com até 4 dígitos, sem o dígito verificador.")
    if not numero_conta or len(numero_conta) > 7:
        raise DomainError("No Bradesco, informe a conta de cobrança com até 7 dígitos.")
    if not carteira or len(carteira) > 2:
        raise DomainError("No Bradesco, informe a carteira de cobrança com até 2 dígitos.")
    if not nosso or len(nosso) > 11:
        raise DomainError("No Bradesco, o Nosso Número deve ter de 1 a 11 dígitos.")

    agencia = agencia.zfill(4)
    numero_conta = numero_conta.zfill(7)
    carteira = carteira.zfill(2)
    nosso = nosso.zfill(11)
    fator = fator_vencimento(vencimento)
    valor_texto = _valor(valor)
    campo_livre = agencia + carteira + nosso + numero_conta + "0"
    sem_dv = "2379" + fator + valor_texto + campo_livre
    dv_geral = digito_codigo_barras(sem_dv)
    codigo = sem_dv[:4] + dv_geral + sem_dv[4:]

    campo1 = "2379" + campo_livre[:5]
    campo2 = campo_livre[5:15]
    campo3 = campo_livre[15:25]
    linha = (
        f"{campo1[:5]}.{campo1[5:]}{digito_modulo_10(campo1)} "
        f"{campo2[:5]}.{campo2[5:]}{digito_modulo_10(campo2)} "
        f"{campo3[:5]}.{campo3[5:]}{digito_modulo_10(campo3)} "
        f"{dv_geral} {fator}{valor_texto}"
    )
    return {
        "nosso_numero": nosso,
        "nosso_numero_dv": digito_nosso_numero(carteira, nosso),
        "codigo_barras": codigo,
        "linha_digitavel": linha,
        "fator_vencimento": fator,
        "carteira": carteira,
        "agencia": agencia,
        "conta": numero_conta,
    }


def preparar_boleto_bradesco(boleto, *, salvar=True, registrar_link=True):
    dados = dados_bancarios_bradesco(
        conta=boleto.conta_bancaria,
        nosso_numero=boleto.nosso_numero,
        vencimento=boleto.data_vencimento,
        valor=boleto.valor,
    )
    boleto.nosso_numero = dados["nosso_numero"]
    boleto.carteira = dados["carteira"]
    boleto.codigo_barras = dados["codigo_barras"]
    boleto.linha_digitavel = dados["linha_digitavel"]
    boleto.url_boleto = url_publica_boleto(boleto)
    if salvar:
        boleto.save(update_fields=[
            "nosso_numero", "carteira", "codigo_barras", "linha_digitavel",
            "url_boleto", "updated_at",
        ])
    if registrar_link:
        TenantPublicLinkService.register(
            kind="boleto", token=str(boleto.public_token), db_alias=get_current_database_alias(),
        )
    return dados


def url_publica_boleto(boleto) -> str:
    caminho = reverse("boleto_publico:visualizar", args=[boleto.public_token])
    return f'{settings.PUBLIC_BASE_URL.rstrip("/")}{caminho}'


def _moeda(valor) -> str:
    return f"{Decimal(valor):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def gerar_pdf_boleto(boleto) -> bytes:
    """Gera as duas partes do boleto e o I25 nas dimensões do manual."""
    from reportlab.graphics import renderPDF
    from reportlab.graphics.barcode import createBarcodeDrawing
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    if not boleto.codigo_barras or not boleto.linha_digitavel:
        preparar_boleto_bradesco(boleto, salvar=False, registrar_link=False)
    if len(_digitos(boleto.codigo_barras)) != 44:
        raise DomainError("O boleto não possui código de barras bancário válido com 44 dígitos.")

    titulo = boleto.conta_receber
    cliente = titulo.cliente
    filial = boleto.filial
    conta = boleto.conta_bancaria
    dados = dados_bancarios_bradesco(
        conta=conta, nosso_numero=boleto.nosso_numero,
        vencimento=boleto.data_vencimento, valor=boleto.valor,
    )
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    largura, altura = A4
    margem = 12 * mm

    def texto(x, y, valor, tamanho=8, negrito=False):
        pdf.setFont("Helvetica-Bold" if negrito else "Helvetica", tamanho)
        pdf.drawString(x, y, str(valor or ""))

    def linha(y):
        pdf.setLineWidth(0.45)
        pdf.line(margem, y, largura - margem, y)

    beneficiario = filial.razao_social
    endereco_beneficiario = " ".join(filter(None, [filial.endereco, filial.numero, filial.bairro, filial.cidade, filial.uf]))
    endereco_pagador = " ".join(filter(None, [cliente.endereco, cliente.numero, cliente.bairro, cliente.cidade, cliente.uf, cliente.cep]))
    agencia_codigo = f'{dados["agencia"]}-{conta.agencia_digito or ""}/{dados["conta"]}-{conta.conta_digito or ""}'
    nosso_exibicao = f'{dados["carteira"]}/{dados["nosso_numero"]}-{dados["nosso_numero_dv"]}'

    # Recibo do pagador.
    y = altura - margem
    texto(margem, y, "BRADESCO | 237-2", 14, True)
    texto(72 * mm, y + 1 * mm, boleto.linha_digitavel, 9, True)
    y -= 9 * mm
    linha(y)
    texto(margem, y - 5 * mm, "Recibo do Pagador", 10, True)
    texto(130 * mm, y - 5 * mm, f"Vencimento: {boleto.data_vencimento:%d/%m/%Y}", 8, True)
    y -= 11 * mm
    texto(margem, y, f"Beneficiário: {beneficiario} — CNPJ {_digitos(filial.cnpj)}", 8)
    texto(130 * mm, y, f"Valor: R$ {_moeda(boleto.valor)}", 8, True)
    y -= 6 * mm
    texto(margem, y, f"Agência/Código: {agencia_codigo}", 8)
    texto(105 * mm, y, f"Nosso Número: {nosso_exibicao}", 8)
    y -= 6 * mm
    texto(margem, y, f"Pagador: {cliente.razao_social} — CPF/CNPJ {_digitos(cliente.cpf_cnpj)}", 8)
    y -= 6 * mm
    texto(margem, y, f"Documento: {boleto.seu_numero}    Emissão: {boleto.data_emissao:%d/%m/%Y}", 8)
    y -= 9 * mm
    linha(y)
    texto(margem, y - 6 * mm, "Autenticação mecânica", 7)

    # Ficha de compensação.
    y -= 22 * mm
    texto(margem, y, "BRADESCO | 237-2", 14, True)
    texto(72 * mm, y + 1 * mm, boleto.linha_digitavel, 9, True)
    y -= 8 * mm
    linha(y)
    texto(margem, y - 5 * mm, "Local de pagamento", 6)
    texto(margem, y - 10 * mm, "Pagável preferencialmente na Rede Bradesco ou no Bradesco Expresso", 8, True)
    texto(156 * mm, y - 5 * mm, "Vencimento", 6)
    texto(156 * mm, y - 10 * mm, boleto.data_vencimento.strftime("%d/%m/%Y"), 9, True)
    y -= 14 * mm
    linha(y)
    texto(margem, y - 5 * mm, "Beneficiário / CNPJ / Endereço", 6)
    texto(margem, y - 10 * mm, f"{beneficiario} — {_digitos(filial.cnpj)} — {endereco_beneficiario}", 7)
    texto(156 * mm, y - 5 * mm, "Agência/Código", 6)
    texto(156 * mm, y - 10 * mm, agencia_codigo, 8, True)
    y -= 14 * mm
    linha(y)
    texto(margem, y - 5 * mm, "Data do documento", 6); texto(margem, y - 10 * mm, boleto.data_emissao.strftime("%d/%m/%Y"), 8)
    texto(48 * mm, y - 5 * mm, "Número do documento", 6); texto(48 * mm, y - 10 * mm, boleto.seu_numero, 8)
    texto(92 * mm, y - 5 * mm, "Espécie", 6); texto(92 * mm, y - 10 * mm, "DM", 8)
    texto(111 * mm, y - 5 * mm, "Aceite", 6); texto(111 * mm, y - 10 * mm, "N", 8)
    texto(130 * mm, y - 5 * mm, "Nosso Número", 6); texto(130 * mm, y - 10 * mm, nosso_exibicao, 8, True)
    y -= 14 * mm
    linha(y)
    texto(margem, y - 5 * mm, "Carteira", 6); texto(margem, y - 10 * mm, dados["carteira"], 8)
    texto(42 * mm, y - 5 * mm, "Moeda", 6); texto(42 * mm, y - 10 * mm, "R$", 8)
    texto(70 * mm, y - 5 * mm, "Instruções de responsabilidade do beneficiário", 6)
    texto(156 * mm, y - 5 * mm, "Valor do documento", 6); texto(156 * mm, y - 10 * mm, f"R$ {_moeda(boleto.valor)}", 9, True)
    y -= 17 * mm
    linha(y)
    texto(margem, y - 5 * mm, "Pagador / CPF ou CNPJ / Endereço", 6)
    texto(margem, y - 10 * mm, f"{cliente.razao_social} — {_digitos(cliente.cpf_cnpj)}", 8, True)
    texto(margem, y - 15 * mm, endereco_pagador, 7)
    y -= 21 * mm
    linha(y)

    barras = createBarcodeDrawing(
        "I2of5", value=_digitos(boleto.codigo_barras), checksum=0,
        humanReadable=False, barHeight=13 * mm, width=103 * mm, height=13 * mm,
    )
    renderPDF.draw(barras, pdf, margem, y - 18 * mm)
    texto(125 * mm, y - 11 * mm, "Autenticação mecânica — Ficha de compensação", 7, True)
    pdf.setTitle(f"Boleto {boleto.seu_numero or boleto.pk}")
    pdf.setAuthor(beneficiario)
    pdf.showPage()
    pdf.save()
    return output.getvalue()
