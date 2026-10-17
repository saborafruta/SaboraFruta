"""Resumo comercial do pedido de venda em PDF e envio pelo WhatsApp."""

import logging
from decimal import Decimal
from html import escape
from io import BytesIO

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from apps.whatsapp_agent.gateway import EvolutionClient, GatewayWhatsAppError
from apps.whatsapp_agent.models import ConversaWhatsApp, MensagemWhatsApp
from apps.whatsapp_agent.notifications import (
    configuracao_whatsapp_conectada,
    numero_whatsapp,
)


logger = logging.getLogger(__name__)

MARINHO = colors.HexColor('#102A43')
AZUL = colors.HexColor('#2563EB')
TEXTO = colors.HexColor('#172033')
CINZA = colors.HexColor('#64748B')
FUNDO = colors.HexColor('#F3F6FA')
BORDA = colors.HexColor('#DCE3EC')
VERDE = colors.HexColor('#047857')


def _texto(valor):
    return escape(str(valor or '').replace('—', '-').replace('–', '-'))


def _brl(valor):
    numero = Decimal(valor or 0)
    return f'R$ {numero:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def _quantidade(valor):
    texto = f'{Decimal(valor or 0):,.3f}'.replace(',', '_').replace('.', ',').replace('_', '.')
    return texto.rstrip('0').rstrip(',')


def _documento(valor):
    digitos = ''.join(caractere for caractere in str(valor or '') if caractere.isdigit())
    if len(digitos) == 14:
        return f'{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}'
    if len(digitos) == 11:
        return f'{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}'
    return str(valor or '')


def _endereco(entidade):
    primeira = ', '.join(filter(None, [
        getattr(entidade, 'endereco', ''),
        getattr(entidade, 'numero', ''),
        getattr(entidade, 'complemento', ''),
    ]))
    segunda = ' - '.join(filter(None, [
        getattr(entidade, 'bairro', ''),
        '/'.join(filter(None, [
            getattr(entidade, 'cidade', ''),
            getattr(entidade, 'uf', ''),
        ])),
    ]))
    return ' | '.join(filter(None, [primeira, segunda])) or 'Não informado'


def _estilos():
    normal = ParagraphStyle(
        'pedido_normal',
        parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10.2,
        textColor=TEXTO,
    )
    return {
        'normal': normal,
        'small': ParagraphStyle(
            'pedido_small', parent=normal, fontSize=7, leading=9, textColor=CINZA,
        ),
        'label': ParagraphStyle(
            'pedido_label', parent=normal, fontSize=6.5, leading=8,
            fontName='Helvetica-Bold', textColor=CINZA,
        ),
        'value': ParagraphStyle(
            'pedido_value', parent=normal, fontName='Helvetica-Bold', fontSize=8.5,
        ),
        'title': ParagraphStyle(
            'pedido_title', parent=normal, fontName='Helvetica-Bold',
            fontSize=22, leading=25, alignment=TA_RIGHT, textColor=MARINHO,
        ),
        'subtitle': ParagraphStyle(
            'pedido_subtitle', parent=normal, fontSize=8, leading=10,
            alignment=TA_RIGHT, textColor=CINZA,
        ),
        'section': ParagraphStyle(
            'pedido_section', parent=normal, fontName='Helvetica-Bold',
            fontSize=9, leading=11, textColor=MARINHO,
        ),
        'th': ParagraphStyle(
            'pedido_th', parent=normal, fontName='Helvetica-Bold',
            fontSize=6.5, leading=8, textColor=colors.white,
        ),
        'td': ParagraphStyle('pedido_td', parent=normal, fontSize=7.2, leading=9),
        'td_right': ParagraphStyle(
            'pedido_td_right', parent=normal, fontSize=7.2, leading=9, alignment=TA_RIGHT,
        ),
        'total': ParagraphStyle(
            'pedido_total', parent=normal, fontName='Helvetica-Bold',
            fontSize=13, leading=16, alignment=TA_RIGHT, textColor=VERDE,
        ),
        'center': ParagraphStyle(
            'pedido_center', parent=normal, fontSize=7, leading=9, alignment=TA_CENTER,
        ),
    }


def _rodape(canvas, documento):
    canvas.saveState()
    largura, _ = A4
    canvas.setStrokeColor(BORDA)
    canvas.line(14 * mm, 13 * mm, largura - 14 * mm, 13 * mm)
    canvas.setFillColor(CINZA)
    canvas.setFont('Helvetica', 6.5)
    canvas.drawString(14 * mm, 8.5 * mm, 'Resumo comercial - não substitui documento fiscal')
    canvas.drawRightString(largura - 14 * mm, 8.5 * mm, f'Página {canvas.getPageNumber()}')
    canvas.restoreState()


class PedidoVendaPdfService:
    """Gera um documento compacto, legível no celular e pronto para impressão."""

    @classmethod
    def gerar(cls, pedido):
        estilos = _estilos()
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=14 * mm,
            rightMargin=14 * mm,
            topMargin=13 * mm,
            bottomMargin=18 * mm,
            title=f'Pedido {pedido.numero_pedido}',
            author=str(pedido.filial),
        )
        filial = pedido.filial
        cliente = pedido.cliente
        nome_filial = filial.nome_fantasia or filial.razao_social
        emitido_em = timezone.localtime(timezone.now()).strftime('%d/%m/%Y às %H:%M')

        empresa = [
            Paragraph(f'<b>{_texto(nome_filial)}</b>', estilos['value']),
            Paragraph(
                f'{_texto(filial.razao_social)}<br/>'
                f'CNPJ {_texto(_documento(filial.cnpj))}<br/>'
                f'{_texto(_endereco(filial))}',
                estilos['small'],
            ),
        ]
        identificacao = [
            Paragraph('RESUMO DO PEDIDO', estilos['title']),
            Paragraph(
                f'<b>{_texto(pedido.numero_pedido)}</b><br/>'
                f'{_texto(pedido.get_status_display())}<br/>'
                f'Emitido em {_texto(emitido_em)}',
                estilos['subtitle'],
            ),
        ]
        cabecalho = Table([[empresa, identificacao]], colWidths=[92 * mm, 90 * mm])
        cabecalho.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))

        def campo(rotulo, valor):
            return [
                Paragraph(_texto(rotulo).upper(), estilos['label']),
                Paragraph(_texto(valor) or 'Não informado', estilos['value']),
            ]

        contato = ' | '.join(filter(None, [cliente.celular, cliente.telefone, cliente.email]))
        dados_cliente = Table([
            [campo('Cliente', cliente.nome_display), campo('CPF/CNPJ', _documento(cliente.cpf_cnpj))],
            [campo('Contato', contato), campo('Entrega', _endereco(cliente))],
        ], colWidths=[72 * mm, 110 * mm])
        dados_cliente.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), FUNDO),
            ('BOX', (0, 0), (-1, -1), .5, BORDA),
            ('INNERGRID', (0, 0), (-1, -1), .5, BORDA),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))

        pagamento = pedido.forma_pagamento.descricao if pedido.forma_pagamento_id else 'Não definido'
        if pedido.condicao_pagamento_id:
            pagamento += f' - {pedido.condicao_pagamento.descricao}'
        if pedido.prazo_pagamento_dias:
            pagamento += f' - {pedido.prazo_pagamento_dias} dias'
        data_entrega = (
            pedido.data_entrega_prevista.strftime('%d/%m/%Y')
            if pedido.data_entrega_prevista else 'Não definida'
        )
        dados_comerciais = Table([[
            campo('Vendedor', pedido.usuario.nome),
            campo('Pagamento', pagamento),
            campo('Entrega prevista', data_entrega),
        ]], colWidths=[50 * mm, 82 * mm, 50 * mm])
        dados_comerciais.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), .5, BORDA),
            ('INNERGRID', (0, 0), (-1, -1), .5, BORDA),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))

        itens = list(pedido.itens.select_related('produto', 'produto__unidade_medida').all())
        linhas = [[
            Paragraph('#', estilos['th']),
            Paragraph('PRODUTO', estilos['th']),
            Paragraph('QUANTIDADE', estilos['th']),
            Paragraph('UNITÁRIO', estilos['th']),
            Paragraph('DESCONTO', estilos['th']),
            Paragraph('TOTAL', estilos['th']),
        ]]
        quantidade_total = Decimal('0')
        subtotal_bruto = Decimal('0')
        for item in itens:
            quantidade = item.quantidade_solicitada or item.quantidade
            quantidade_total += quantidade
            subtotal_bruto += item.valor_bruto
            codigo = item.produto.codigo or str(item.produto_id)
            descricao = f'<b>{_texto(item.produto.descricao)}</b><br/><font color="#64748B">Cód. {_texto(codigo)}</font>'
            linhas.append([
                Paragraph(str(item.numero_item), estilos['center']),
                Paragraph(descricao, estilos['td']),
                Paragraph(
                    f'{_quantidade(quantidade)} {_texto(item.produto.unidade_medida.sigla)}',
                    estilos['td_right'],
                ),
                Paragraph(_brl(item.valor_unitario), estilos['td_right']),
                Paragraph(_brl(item.valor_desconto), estilos['td_right']),
                Paragraph(f'<b>{_brl(item.valor_total)}</b>', estilos['td_right']),
            ])
        tabela_itens = Table(
            linhas,
            colWidths=[8 * mm, 72 * mm, 22 * mm, 26 * mm, 24 * mm, 30 * mm],
            repeatRows=1,
        )
        tabela_itens.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), MARINHO),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, FUNDO]),
            ('LINEBELOW', (0, 1), (-1, -1), .35, BORDA),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))

        resumo_itens = Paragraph(
            f'{len(itens)} produto(s) - {_quantidade(quantidade_total)} unidade(s) no total',
            estilos['small'],
        )
        totais = [
            ['Subtotal dos itens', _brl(subtotal_bruto)],
            ['Descontos', f'- {_brl(pedido.valor_desconto)}'],
        ]
        if pedido.valor_frete:
            totais.append(['Frete', _brl(pedido.valor_frete)])
        if pedido.valor_acrescimo or pedido.valor_outras_despesas:
            totais.append([
                'Acréscimos e outras despesas',
                _brl(pedido.valor_acrescimo + pedido.valor_outras_despesas),
            ])
        totais.append([
            Paragraph('<b>TOTAL DO PEDIDO</b>', estilos['value']),
            Paragraph(_brl(pedido.valor_total), estilos['total']),
        ])
        tabela_totais = Table(totais, colWidths=[55 * mm, 38 * mm], hAlign='RIGHT')
        tabela_totais.setStyle(TableStyle([
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('TEXTCOLOR', (0, 0), (-1, -2), CINZA),
            ('FONTNAME', (0, 0), (-1, -2), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -2), 8),
            ('LINEABOVE', (0, -1), (-1, -1), 1, AZUL),
            ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#EFF6FF')),
            ('LEFTPADDING', (0, 0), (-1, -1), 7),
            ('RIGHTPADDING', (0, 0), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        historia = [
            cabecalho,
            Spacer(1, 7 * mm),
            Paragraph('DADOS DO CLIENTE', estilos['section']),
            Spacer(1, 2 * mm),
            dados_cliente,
            Spacer(1, 3 * mm),
            dados_comerciais,
            Spacer(1, 7 * mm),
            Paragraph('PRODUTOS DO PEDIDO', estilos['section']),
            Spacer(1, 2 * mm),
            tabela_itens,
            Spacer(1, 2 * mm),
            resumo_itens,
            Spacer(1, 5 * mm),
            KeepTogether([tabela_totais]),
        ]
        if pedido.observacao:
            historia.extend([
                Spacer(1, 6 * mm),
                Paragraph('OBSERVAÇÕES', estilos['section']),
                Spacer(1, 2 * mm),
                Table(
                    [[Paragraph(_texto(pedido.observacao).replace('\n', '<br/>'), estilos['normal'])]],
                    colWidths=[182 * mm],
                    style=TableStyle([
                        ('BACKGROUND', (0, 0), (-1, -1), FUNDO),
                        ('BOX', (0, 0), (-1, -1), .5, BORDA),
                        ('LEFTPADDING', (0, 0), (-1, -1), 8),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                        ('TOPPADDING', (0, 0), (-1, -1), 7),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
                    ]),
                ),
            ])
        doc.build(historia, onFirstPage=_rodape, onLaterPages=_rodape)
        return buffer.getvalue()


def nome_arquivo_pedido(pedido):
    numero = ''.join(caractere for caractere in pedido.numero_pedido if caractere.isalnum() or caractere in '-_')
    return f'pedido-{numero or pedido.pk}.pdf'


def _enviar_pdf_pedido_whatsapp(pedido, *, db_alias):
    telefone = numero_whatsapp(pedido.cliente.celular or pedido.cliente.telefone)
    if not telefone:
        return False, 'O cliente não possui um número de WhatsApp válido.'

    configuracao = configuracao_whatsapp_conectada(pedido.filial, db_alias)
    if not configuracao:
        return False, 'O WhatsApp desta filial não está conectado.'

    remote_jid = f'{telefone}@s.whatsapp.net'
    conversa = (
        ConversaWhatsApp.objects.using('default')
        .filter(configuracao_id=configuracao.pk, remote_jid=remote_jid)
        .order_by('-ativa', '-ultima_mensagem_em', '-created_at')
        .first()
    )
    if not conversa:
        conversa = ConversaWhatsApp.objects.using('default').create(
            configuracao_id=configuracao.pk,
            filial_id=configuracao.filial_id,
            remote_jid=remote_jid,
            telefone=telefone,
            nome_contato=pedido.cliente.nome_display,
            ativa=False,
            etapa='pedido_enviado',
            etapa_crm=ConversaWhatsApp.EtapaCRM.INTERESSADO,
        )
    conversa.ultima_mensagem_em = timezone.now()
    conversa.save(using='default', update_fields=['ultima_mensagem_em', 'updated_at'])

    legenda = (
        f'Olá, {pedido.cliente.nome_display}! Segue o resumo do pedido '
        f'{pedido.numero_pedido}, no valor de {_brl(pedido.valor_total)}.'
    )
    saida = MensagemWhatsApp.objects.using('default').create(
        conversa_id=conversa.pk,
        direcao=MensagemWhatsApp.Direcao.SAIDA,
        texto=legenda,
        tipo='pdf_pedido_venda',
        status='pendente',
        dados_evento={'pedido_id': pedido.pk, 'arquivo': nome_arquivo_pedido(pedido)},
    )
    try:
        retorno = EvolutionClient(configuracao).enviar_documento(
            telefone,
            PedidoVendaPdfService.gerar(pedido),
            nome_arquivo_pedido(pedido),
            legenda,
        )
        saida.identificador_externo = str((retorno.get('key') or {}).get('id') or '')
        saida.status = 'enviada'
        mensagem = f'PDF do pedido enviado para o WhatsApp do cliente ({telefone}).'
        enviado = True
    except GatewayWhatsAppError as exc:
        saida.status = 'erro'
        saida.dados_evento = {
            **saida.dados_evento,
            'erro': str(exc),
        }
        mensagem = f'Não foi possível enviar o PDF pelo WhatsApp: {exc}'
        enviado = False
        logger.warning('Falha ao enviar PDF do pedido %s: %s', pedido.pk, exc)
    except Exception as exc:
        saida.status = 'erro'
        saida.dados_evento = {
            **saida.dados_evento,
            'erro': str(exc),
        }
        mensagem = 'Não foi possível gerar ou enviar o PDF pelo WhatsApp agora.'
        enviado = False
        logger.exception('Falha inesperada ao gerar ou enviar PDF do pedido %s.', pedido.pk)
    saida.save(
        using='default',
        update_fields=['identificador_externo', 'status', 'dados_evento', 'updated_at'],
    )
    return enviado, mensagem


def enviar_pdf_pedido_whatsapp(pedido, *, db_alias):
    """Envia sem permitir que uma falha externa desfaça o pedido já criado."""
    try:
        return _enviar_pdf_pedido_whatsapp(pedido, db_alias=db_alias)
    except Exception:
        logger.exception('Falha inesperada ao enviar o PDF do pedido %s.', pedido.pk)
        return False, 'Não foi possível enviar o PDF pelo WhatsApp agora.'
