"""Dados comerciais compartilhados pela página pública e seu PDF."""
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from django.utils import timezone
from django.core.exceptions import ObjectDoesNotExist

CENTAVO = Decimal('0.01')


def moeda(valor):
    return f'{valor:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def percentual(valor, base):
    return (valor * 100 / base).quantize(CENTAVO, rounding=ROUND_HALF_UP) if base else Decimal(0)


def dados_comprovante(venda):
    filial = venda.filial
    try:
        params = filial.parametros_sistema
    except ObjectDoesNotExist:
        params = None
    arquivo_logo = getattr(params, 'logo', None) or filial.imagem
    logo_url = getattr(params, 'logo_url', '') or (arquivo_logo.url if arquivo_logo else '') or filial.empresa.logo_url
    if urlsplit(logo_url).scheme not in ('', 'https', 'http'):
        logo_url = ''
    itens = []
    bruto = desconto_itens = Decimal(0)
    for item in venda.itens.all():
        base = (item.quantidade * item.valor_unitario).quantize(CENTAVO, rounding=ROUND_HALF_UP)
        desconto = min(base, max(Decimal(0), item.desconto_valor))
        bruto += base
        desconto_itens += desconto
        itens.append({
            'nome': item.produto.descricao, 'quantidade': format(item.quantidade.normalize(), 'f').replace('.', ','),
            'unidade': item.unidade_medida, 'unitario': moeda(item.valor_unitario),
            'total': moeda(item.valor_total), 'desconto': moeda(desconto),
            'tem_desconto': desconto > 0, 'percentual': moeda(percentual(desconto, base)),
            'observacao': item.observacao or '',
        })
    resumo = [('Subtotal bruto', moeda(bruto))]
    if desconto_itens:
        resumo.append((f'Desconto nos itens ({moeda(percentual(desconto_itens, bruto))}%)', '- ' + moeda(desconto_itens)))
    if venda.valor_desconto:
        resumo.append((f'Desconto geral ({moeda(percentual(venda.valor_desconto, bruto - desconto_itens))}%)', '- ' + moeda(venda.valor_desconto)))
    if venda.valor_acrescimo:
        resumo.append(('Acréscimo', moeda(venda.valor_acrescimo)))
    resumo.append(('TOTAL', moeda(venda.valor_total)))
    pagamentos = [(p.forma_pagamento.descricao, moeda(p.valor)) for p in venda.pagamentos.all()]
    if venda.troco:
        pagamentos.append(('Troco', moeda(venda.troco)))
    return {
        'empresa': filial.nome_fantasia or filial.razao_social,
        'logo_url': logo_url, 'arquivo_logo': arquivo_logo,
        'numero': f'{venda.numero_venda:06d}',
        'data': timezone.localtime(venda.data_venda).strftime('%d/%m/%Y %H:%M'),
        'cliente': venda.cliente.razao_social if venda.cliente else 'Consumidor Final',
        'itens': itens, 'resumo': resumo, 'pagamentos': pagamentos,
    }


def gerar_pdf(venda):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    cupom = dados_comprovante(venda)
    output = BytesIO()
    normal = ParagraphStyle('texto', fontName='Helvetica', fontSize=8, leading=11)
    negrito = ParagraphStyle('negrito', parent=normal, fontName='Helvetica-Bold')
    titulo = ParagraphStyle('titulo', parent=normal, fontName='Helvetica-Bold', fontSize=11, leading=15, alignment=1)
    direita = ParagraphStyle('valor', parent=normal, alignment=2)
    blocos = []
    arquivo = cupom['arquivo_logo']
    if arquivo:
        try:
            with arquivo.open('rb') as stream:
                raw = BytesIO(stream.read(5 * 1024 * 1024))
            marca = Image(raw, width=30*mm, height=18*mm, kind='proportional')
            marca.hAlign = 'CENTER'
            blocos.extend([marca, Spacer(1, 4*mm)])
        except (OSError, ValueError):
            pass

    def texto(value, style=normal):
        return Paragraph(escape(str(value)), style)

    def par(rotulo, valor):
        tabela = Table([[texto(rotulo), texto(valor, direita)]], colWidths=[42*mm, 26*mm])
        tabela.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        return tabela

    blocos.extend([texto(cupom['empresa'], titulo), Spacer(1, 4*mm),
                   par('Venda', '#' + cupom['numero']), par('Data', cupom['data']),
                   par('Cliente', cupom['cliente']), Spacer(1, 3*mm)])
    for item in cupom['itens']:
        blocos.extend([texto(item['nome']),
                       par(f"{item['quantidade']} {item['unidade']} × R$ {item['unitario']}", f"R$ {item['total']}")])
        if item['tem_desconto']:
            blocos.append(texto(f"Desconto {item['percentual']}%: - R$ {item['desconto']}"))
        if item['observacao']:
            blocos.append(texto(f"Observação: {item['observacao']}", negrito))
        blocos.append(Spacer(1, 3*mm))
    for rotulo, valor in cupom['resumo'] + cupom['pagamentos']:
        exibicao = ('- R$ ' + valor[2:]) if valor.startswith('- ') else ('R$ ' + valor)
        blocos.append(par(rotulo, exibicao))
    blocos.extend([Spacer(1, 4*mm), texto('Este comprovante não é um documento fiscal.')])
    altura_observacoes = sum(min(50, 8 + len(item['observacao']) / 12 * 3.8) for item in cupom['itens'] if item['observacao'])
    altura = max(150, min(400, 90 + len(cupom['itens'])*24 + len(cupom['pagamentos'])*10 + altura_observacoes))
    doc = SimpleDocTemplate(output, pagesize=(80*mm, altura*mm),
                           leftMargin=6*mm, rightMargin=6*mm, topMargin=8*mm, bottomMargin=8*mm,
                           title=f"Comprovante #{cupom['numero']}", author=cupom['empresa'])
    doc.build(blocos)
    return output.getvalue()
