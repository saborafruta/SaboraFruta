"""Proposta comercial com foto, especificações, grade e nomes por produto.

Layout exclusivo do orçamento. A ficha de produção mantém seu desenho.
Dados e imagens vêm do pedido e da filial, nunca da imagem de referência.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
    TableStyle,
)

from .pdf_marca import LARGURA_UTIL, MARGEM, cnpj, esc, logo
from .pedido_pdf import DESENHAVEIS, PedidoPdfService, _imagem

MARINHO = colors.HexColor('#052344')
TEXTO = colors.HexColor('#172033')
CINZA = colors.HexColor('#667085')
FUNDO = colors.HexColor('#f4f5f7')
BORDA = colors.HexColor('#d9dde3')
MESES = (
    'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
    'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
)


def brl(valor):
    return f'R${valor or Decimal("0"):,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def _por_extenso(data):
    return f'{data.day} de {MESES[data.month - 1]} de {data.year}'


def _texto(valor):
    return esc(str(valor or '').replace('—', '-').replace('–', '-'))


def _estilos():
    normal = ParagraphStyle(
        'orc_normal', parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica', fontSize=8, leading=11, textColor=TEXTO,
    )
    return {
        'normal': normal,
        'td': normal,
        'td_dir': ParagraphStyle('orc_dir', parent=normal, alignment=2),
        'centro': ParagraphStyle('orc_centro', parent=normal, alignment=1),
        'nome': ParagraphStyle('orc_nome', parent=normal, fontName='Helvetica-Bold'),
        'th': ParagraphStyle(
            'orc_th', parent=normal, fontSize=6, leading=8,
            fontName='Helvetica-Bold', textColor=colors.white,
        ),
        'pequeno': ParagraphStyle(
            'orc_pequeno', parent=normal, fontSize=7, leading=9, textColor=CINZA,
        ),
        'celula': ParagraphStyle('orc_celula', parent=normal, fontSize=7, leading=9),
        'secao': ParagraphStyle(
            'orc_secao', parent=normal, fontName='Helvetica-Bold', textColor=MARINHO,
        ),
        'secao_numero': ParagraphStyle('orc_numero', parent=normal),
        'titulo': ParagraphStyle(
            'orc_titulo', parent=normal, fontName='Helvetica-Bold',
            fontSize=29, leading=34, alignment=2, textColor=MARINHO,
        ),
    }


def _estilo_tabela(padding=6):
    return [
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), padding),
        ('RIGHTPADDING', (0, 0), (-1, -1), padding),
        ('TOPPADDING', (0, 0), (-1, -1), padding),
        ('BOTTOMPADDING', (0, 0), (-1, -1), padding),
    ]


class OrcamentoPdfService:
    # Cada linha contém um produto inteiro, inclusive imagens e nomes.
    LARGURAS = [27 * mm, LARGURA_UTIL - 98 * mm, 18 * mm, 26 * mm, 27 * mm]

    @classmethod
    def gerar(cls, pedido):
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=MARGEM, rightMargin=MARGEM,
            topMargin=10 * mm, bottomMargin=22 * mm,
            title=f'Orçamento {pedido.numero:06d}', author=str(pedido.filial),
        )
        e = _estilos()
        elementos = cls._empresa(pedido, e) + cls._cliente(pedido, e)
        elementos += cls._itens(pedido, e)
        elementos += PedidoPdfService._artes_do_pedido(
            pedido, e, LARGURA_UTIL, cor=MARINHO, cor_clara=FUNDO,
            arredondada=True,
        )
        elementos += [Spacer(1, 9), cls._totais(pedido, e), Spacer(1, 9)]
        elementos += cls._pagamento_previsto(pedido, e)
        elementos += [Spacer(1, 6)]
        elementos += cls._fechamento(pedido, e)
        doc.build(elementos, onFirstPage=cls._rodape, onLaterPages=cls._rodape)
        return buffer.getvalue()

    @staticmethod
    def _rodape(canvas, doc):
        canvas.saveState()
        largura = canvas._pagesize[0]
        canvas.setStrokeColor(MARINHO)
        canvas.line(MARGEM, 17 * mm, largura - MARGEM, 17 * mm)
        canvas.setFillColor(CINZA)
        canvas.setFont('Helvetica', 7)
        canvas.drawCentredString(
            largura / 2, 12 * mm,
            'Agradecemos a oportunidade e estamos à disposição para esclarecer quaisquer dúvidas.',
        )
        canvas.setFont('Helvetica', 6)
        canvas.drawRightString(largura - MARGEM, 8 * mm, f'Página {doc.page}')
        canvas.setFillColor(MARINHO)
        canvas.rect(0, 0, largura, 4 * mm, fill=1, stroke=0)
        canvas.restoreState()

    @staticmethod
    def _empresa(pedido, e):
        filial = pedido.filial
        marca = logo(filial, 43 * mm, 25 * mm)
        direita = [
            Paragraph('ORÇAMENTO', e['titulo']), Spacer(1, 5),
            HRFlowable(width='100%', color=MARINHO, thickness=1.2),
            Spacer(1, 8),
            Paragraph(f'<b>{_texto(filial.nome_fantasia or filial.razao_social)}</b>', e['td_dir']),
        ]
        if filial.cnpj:
            direita += [
                Spacer(1, 3),
                Paragraph(f'CNPJ: {esc(cnpj(filial.cnpj))}', e['td_dir']),
            ]
        if filial.nome_fantasia and filial.nome_fantasia != filial.razao_social:
            direita.append(Paragraph(
                _texto(filial.razao_social),
                ParagraphStyle('orc_razao', parent=e['pequeno'], alignment=2),
            ))
        tabela = Table(
            [[marca or '', direita]],
            colWidths=[LARGURA_UTIL * .42, LARGURA_UTIL * .58],
        )
        tabela.setStyle(TableStyle(_estilo_tabela(0)))
        return [tabela, Spacer(1, 10)]

    @staticmethod
    def _cliente(pedido, e):
        cliente = pedido.cliente
        nome = getattr(cliente, 'razao_social', None) or str(cliente)
        documento = _texto(getattr(cliente, 'cpf_cnpj', '')) or 'Não informado'
        celulas = [
            Paragraph(f'<b>Cliente:</b><br/>{_texto(nome)}', e['normal']),
            Paragraph(f'<b>CNPJ/CPF:</b><br/>{documento}', e['normal']),
            Paragraph(f'<b>Data de Emissão:</b><br/>{pedido.data_pedido:%d/%m/%Y}', e['normal']),
        ]
        tabela = Table(
            [celulas],
            colWidths=[LARGURA_UTIL * .43, LARGURA_UTIL * .3, LARGURA_UTIL * .27],
            cornerRadii=[6] * 4,
        )
        tabela.setStyle(TableStyle(_estilo_tabela(9) + [
            ('BACKGROUND', (0, 0), (-1, -1), FUNDO),
        ]))
        return [tabela, Spacer(1, 10)]

    @staticmethod
    def _especificacoes(item):
        """Não trunca a estrutura: cada opção comercial aparece em sua linha."""
        pares = []
        for rotulo, valor in (
            ('Referência', item.referencia), ('Tecido / Malha', item.tecido),
            ('Cor', item.cor), ('Gola', item.get_gola_display() if item.gola else ''),
            ('Manga', item.get_manga_display() if item.manga else ''),
            ('Acabamento', item.acabamento),
        ):
            if valor:
                pares.append((rotulo, str(valor)))
        texto = (item.observacoes or '').replace('Estrutura da peça:', '').strip()
        for linha in texto.splitlines():
            if not linha.strip():
                continue
            rotulo, sep, valor = linha.partition(':')
            pares.append(
                (rotulo.strip(), valor.strip()) if sep
                else ('Observação', linha.strip())
            )
        for p in item.personalizacoes.all():
            valores = [
                str(p), p.nome_personalizado, p.numero_personalizado,
                p.patrocinios, p.observacoes,
            ]
            if p.arquivo and p.extensao not in DESENHAVEIS:
                valores.append(p.nome_arquivo)
            pares.append(('Impressão / arte', ' - '.join(str(v) for v in valores if v)))
        return list(dict.fromkeys(pares))

    @staticmethod
    def _fotos(item, e, altura=29 * mm):
        fotos = []
        for visual in item.visuais.all():
            campo = visual.imagem or (
                visual.mockup.imagem if visual.mockup_id else None
            )
            imagem = _imagem(campo, 23 * mm, altura)
            if imagem is not None:
                fotos += [imagem, Spacer(1, 5)]
            elif campo:
                fotos.append(Paragraph('Imagem indisponível', e['pequeno']))
        for p in item.personalizacoes.all():
            if p.arquivo and p.extensao in DESENHAVEIS:
                imagem = _imagem(p.arquivo, 23 * mm, 25 * mm)
                fotos += [
                    imagem or Paragraph('Arte indisponível', e['pequeno']),
                    Spacer(1, 5),
                ]
        return fotos or ''

    @staticmethod
    def _pessoas(item, e, largura):
        pessoas = list(item.individuais.all())
        if not pessoas:
            return []
        metade = (len(pessoas) + 1) // 2
        dados = [[Paragraph(f'<b>{titulo}</b>', e['celula']) for titulo in (
            'NOME / NÚMERO', 'TAM.', 'NOME / NÚMERO', 'TAM.',
        )]]
        cabecalhos = 1
        if len(pessoas) > 24:
            dados.insert(0, [Paragraph(_texto(item.nome_exibicao), e['nome']), '', '', ''])
            cabecalhos = 2
        for indice in range(metade):
            linha = []
            for posicao in (indice, indice + metade):
                if posicao >= len(pessoas):
                    linha += ['', '']
                    continue
                p = pessoas[posicao]
                nome = ' '.join(v for v in (p.nome, p.numero) if v) or '-'
                if p.observacoes:
                    nome += f' ({p.observacoes})'
                linha += [
                    Paragraph(_texto(nome), e['celula']),
                    Paragraph(_texto(p.tamanho.sigla), e['celula']),
                ]
            dados.append(linha)
        tabela = Table(
            dados, colWidths=[largura * .36, largura * .14] * 2,
            repeatRows=cabecalhos, cornerRadii=[4] * 4,
        )
        tabela.setStyle(TableStyle(_estilo_tabela(3) + [
            ('GRID', (0, 0), (-1, -1), .4, BORDA),
            ('BACKGROUND', (0, 0), (-1, cabecalhos - 1), FUNDO),
        ]))
        if cabecalhos == 2:
            tabela.setStyle(TableStyle([('SPAN', (0, 0), (-1, 0))]))
        return [
            Spacer(1, 7),
            HRFlowable(width='100%', thickness=.5, color=BORDA),
            Spacer(1, 6),
            Paragraph(f'<b>Personalização por pessoa - {len(pessoas)}:</b>', e['celula']),
            Spacer(1, 5), tabela,
        ]

    @classmethod
    def _produto(cls, item, e):
        largura = cls.LARGURAS[1] - 12
        conteudo = [
            Paragraph(_texto(item.nome_exibicao), e['nome']), Spacer(1, 7),
        ]
        especificacoes = cls._especificacoes(item)
        for rotulo, valor in especificacoes:
            conteudo += [
                Paragraph(f'<b>{_texto(rotulo)}:</b> {_texto(valor)}', e['celula']),
                Spacer(1, 2),
            ]
        grade = [g for g in item.grade.all() if g.quantidade]
        if grade:
            resumo = ' | '.join(f'{g.tamanho.sigla} {g.quantidade}' for g in grade)
            conteudo += [
                Spacer(1, 6),
                HRFlowable(width='100%', thickness=.5, color=BORDA),
                Spacer(1, 6),
                Paragraph(
                    f'<b>Grade:</b> {_texto(resumo)} | '
                    f'<b>Total {sum(g.quantidade for g in grade)}</b>', e['celula'],
                ),
            ]
        conteudo += cls._pessoas(item, e, largura)
        return [
            cls._fotos(item, e, (29 if especificacoes or grade else 13) * mm), conteudo,
            Paragraph(str(item.quantidade), e['centro']),
            Paragraph(brl(item.valor_unitario), e['td_dir']),
            Paragraph(brl(item.subtotal), e['td_dir']),
        ]

    @classmethod
    def _itens(cls, pedido, e):
        cabecalho = [Paragraph(t, e['th']) for t in (
            'PRODUTO / ESPECIFICAÇÕES', '', 'QUANTIDADE', 'VALOR UNITÁRIO', 'SUBTOTAL',
        )]
        cabecalho[2] = Paragraph('QUANTIDADE', ParagraphStyle(
            'orc_th_quantidade', parent=e['th'], fontSize=5.3,
        ))
        dados = [cabecalho] + [cls._produto(item, e) for item in pedido.itens.all()]
        if len(dados) == 1:
            return []
        # Divide dentro da linha apenas quando um produto excede uma página.
        tabela = Table(
            dados, colWidths=cls.LARGURAS, repeatRows=1,
            splitByRow=1, splitInRow=1, cornerRadii=[5] * 4,
        )
        tabela.setStyle(TableStyle(_estilo_tabela(6) + [
            ('SPAN', (0, 0), (1, 0)),
            ('BACKGROUND', (0, 0), (-1, 0), MARINHO),
            ('BOX', (0, 0), (-1, -1), .5, BORDA),
            ('LINEBELOW', (0, 1), (-1, -1), .5, BORDA),
            ('TOPPADDING', (0, 1), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 6),
        ]))
        return [tabela]

    @staticmethod
    def _totais(pedido, e):
        linhas = [[
            Paragraph('Subtotal:', e['nome']),
            Paragraph(brl(pedido.subtotal), e['td_dir']),
        ]]
        for rotulo, valor in (
            ('Desconto', pedido.desconto), ('Acréscimo', pedido.acrescimo),
            ('Frete', pedido.frete),
        ):
            if valor:
                linhas.append([
                    Paragraph(f'{rotulo}:', e['normal']),
                    Paragraph(brl(valor), e['td_dir']),
                ])
        total = len(linhas)
        destaque = ParagraphStyle(
            'orc_total', parent=e['td_dir'], fontName='Helvetica-Bold',
            fontSize=17, leading=21, textColor=colors.white,
        )
        linhas.append([
            Paragraph('<font color="white"><b>Total:</b></font>', e['normal']),
            Paragraph(brl(pedido.valor_total), destaque),
        ])
        if pedido.entrada:
            linhas += [
                [Paragraph('Entrada:', e['normal']), Paragraph(brl(pedido.entrada), e['td_dir'])],
                [Paragraph('Saldo:', e['normal']), Paragraph(brl(pedido.saldo), e['td_dir'])],
            ]
        tabela = Table(
            linhas, colWidths=[33 * mm, 43 * mm],
            hAlign='RIGHT', cornerRadii=[5] * 4,
        )
        tabela.setStyle(TableStyle(_estilo_tabela(7) + [
            ('BACKGROUND', (0, 0), (-1, -1), FUNDO),
            ('BACKGROUND', (0, total), (-1, total), MARINHO),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        return tabela

    @classmethod
    def _pagamento_previsto(cls, pedido, e):
        linhas = list(pedido.previsao_pagamento or [])
        if not linhas:
            return [Paragraph(
                '<b>Forma de pagamento prevista:</b> Não informada.', e['normal'],
            )]
        rotulos = dict(pedido.FormaPagamentoPrevista.choices)
        dados = [[
            Paragraph('FORMA DE PAGAMENTO PREVISTA', e['th']),
            Paragraph('VALOR', e['th']),
        ]]
        for linha in linhas:
            try:
                valor = Decimal(str(linha.get('valor') or '0'))
                if not valor.is_finite():
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                valor = Decimal('0')
            dados.append([
                Paragraph(_texto(rotulos.get(linha.get('forma'), linha.get('forma'))), e['normal']),
                Paragraph(brl(valor), e['td_dir']),
            ])
        tabela = Table(
            dados, colWidths=[LARGURA_UTIL - 38 * mm, 38 * mm],
            repeatRows=1, cornerRadii=[5] * 4,
        )
        tabela.setStyle(TableStyle(_estilo_tabela(5) + [
            ('BACKGROUND', (0, 0), (-1, 0), MARINHO),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, FUNDO]),
            ('BOX', (0, 0), (-1, -1), .5, BORDA),
        ]))
        return [tabela]

    @staticmethod
    def _observacoes(pedido, e, largura=LARGURA_UTIL):
        textos = [
            'Este orçamento é válido por 5 dias a partir da data de emissão.',
            'O prazo máximo de entrega é de até 30 dias úteis após a aprovação do orçamento.',
        ]
        if pedido.condicao_pagamento_id:
            textos.append(f'Condição: {esc(pedido.condicao_pagamento)}')
        textos += [
            _texto(l.strip()) for l in (pedido.observacoes or '').splitlines()
            if l.strip()
        ]
        dados = [[Paragraph('Observações e prazos:', e['secao'])]]
        dados += [[Paragraph(f'- {texto}', e['normal'])] for texto in textos]
        tabela = Table(dados, colWidths=[largura], cornerRadii=[5] * 4)
        tabela.setStyle(TableStyle(_estilo_tabela(3) + [
            ('BOX', (0, 0), (-1, -1), .5, BORDA),
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fafbfc')),
        ]))
        return [tabela]

    @staticmethod
    def _previsao_entrega(pedido, e):
        data = pedido.data_prevista_entrega
        return [
            Paragraph('Previsão de entrega:', e['secao']), Spacer(1, 5),
            Paragraph(f'<b>{data:%d/%m/%Y}</b>' if data else 'A combinar', e['normal']),
            Spacer(1, 10),
            HRFlowable(width='100%', thickness=.5, color=BORDA),
            Spacer(1, 10),
            Paragraph(_por_extenso(pedido.data_pedido), e['normal']),
        ]

    @classmethod
    def _fechamento(cls, pedido, e):
        largura_obs = LARGURA_UTIL * .62
        tabela = Table([[
            cls._observacoes(pedido, e, largura_obs - 10),
            cls._previsao_entrega(pedido, e),
        ]], colWidths=[largura_obs, LARGURA_UTIL - largura_obs], splitInRow=1)
        tabela.setStyle(TableStyle(_estilo_tabela(5)))
        return [KeepTogether([tabela])]
