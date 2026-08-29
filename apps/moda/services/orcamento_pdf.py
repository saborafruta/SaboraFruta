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
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, HRFlowable, KeepTogether, PageTemplate,
    PageBreak, Paragraph, Spacer, Table, TableStyle,
)

from .pdf_marca import LARGURA_UTIL, MARGEM, cnpj, esc, logo
from .pedido_pdf import DESENHAVEIS, PedidoPdfService, _imagem

MARINHO = colors.HexColor('#052344')
TEXTO = colors.HexColor('#172033')
CINZA = colors.HexColor('#667085')
FUNDO = colors.HexColor('#f4f5f7')
BORDA = colors.HexColor('#d9dde3')
OBSERVACAO_PAGAMENTO_ORCAMENTO = (
    'O pagamento de 50% do valor total deverá ser realizado na aprovação '
    'do pedido, para início da produção. Os 50% restantes deverão ser pagos '
    'no ato da entrega.'
)
TOPO_CONTINUACAO = 21 * mm
MARGEM_INFERIOR = 22 * mm
ALTURA_CONTINUACAO = A4[1] - TOPO_CONTINUACAO - MARGEM_INFERIOR - 12


def especificacoes_orcamento_item(item):
    """Dados comerciais da peça, compartilhados pelo PDF e pelo link público."""
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
        par = (
            (rotulo.strip(), valor.strip()) if sep
            else ('Observação', linha.strip())
        )
        chave = par[0].casefold()
        pares = [existente for existente in pares if existente[0].casefold() != chave]
        pares.append(par)
    for personalizacao in item.personalizacoes.all():
        valores = [
            str(personalizacao), personalizacao.nome_personalizado,
            personalizacao.numero_personalizado, personalizacao.patrocinios,
            personalizacao.observacoes,
        ]
        if personalizacao.arquivo and personalizacao.extensao not in DESENHAVEIS:
            valores.append(personalizacao.nome_arquivo)
        pares.append((
            'Impressão / arte',
            ' - '.join(str(valor) for valor in valores if valor),
        ))
    return list(dict.fromkeys(pares))


class _TabelaProdutos(Table):
    """Só divide um produto se ele exceder uma página inteira de continuação."""

    def split(self, availWidth, availHeight):
        self.splitInRow = 0
        partes = super().split(availWidth, availHeight)
        if partes:
            return partes
        cabecalhos = self.repeatRows or 0
        if sum(self._rowHeights[:cabecalhos + 1]) <= ALTURA_CONTINUACAO:
            return []  # O produto inteiro começa na próxima página.
        self.splitInRow = 1
        try:
            return super().split(availWidth, availHeight)
        finally:
            self.splitInRow = 0


class _NumeroObservacao(Flowable):
    def __init__(self, numero):
        super().__init__()
        self.numero = numero
        self.width = 18
        self.height = 17

    def draw(self):
        self.canv.setFillColor(MARINHO)
        self.canv.circle(8, 9, 8, fill=1, stroke=0)
        self.canv.setFillColor(colors.white)
        self.canv.setFont('Helvetica-Bold', 7)
        self.canv.drawCentredString(8, 6.5, str(self.numero))


def brl(valor):
    return f'R${valor or Decimal("0"):,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def _texto(valor):
    return esc(str(valor or '').replace('—', '-').replace('–', '-'))


def observacoes_orcamento(pedido):
    """Textos comerciais compartilhados pelo PDF e pelo link do WhatsApp."""
    textos = [
        'Este orçamento é válido por 5 dias a partir da data de emissão.',
        'O prazo máximo de entrega é de até 30 dias após a aprovação do orçamento.',
    ]
    if pedido.condicao_pagamento_id:
        textos.append(f'Condição: {pedido.condicao_pagamento}')
    textos += [
        linha.strip() for linha in (pedido.observacoes or '').splitlines()
        if linha.strip()
    ]
    return textos


def _estilos():
    normal = ParagraphStyle(
        'orc_normal', parent=getSampleStyleSheet()['Normal'],
        fontName='Helvetica', fontSize=7.4, leading=9.2, textColor=TEXTO,
    )
    return {
        'normal': normal,
        'td': normal,
        'td_dir': ParagraphStyle('orc_dir', parent=normal, alignment=2),
        'centro': ParagraphStyle('orc_centro', parent=normal, alignment=1),
        'nome': ParagraphStyle('orc_nome', parent=normal, fontName='Helvetica-Bold'),
        'th': ParagraphStyle(
            'orc_th', parent=normal, fontSize=5.6, leading=7,
            fontName='Helvetica-Bold', textColor=colors.white,
        ),
        'pequeno': ParagraphStyle(
            'orc_pequeno', parent=normal, fontSize=6.5, leading=8, textColor=CINZA,
        ),
        'celula': ParagraphStyle('orc_celula', parent=normal, fontSize=6.5, leading=8),
        'secao': ParagraphStyle(
            'orc_secao', parent=normal, fontName='Helvetica-Bold', textColor=MARINHO,
        ),
        'secao_numero': ParagraphStyle('orc_numero', parent=normal),
        'titulo': ParagraphStyle(
            'orc_titulo', parent=normal, fontName='Helvetica-Bold',
            fontSize=25, leading=29, alignment=2, textColor=MARINHO,
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
        doc = BaseDocTemplate(
            buffer, pagesize=A4, leftMargin=MARGEM, rightMargin=MARGEM,
            topMargin=10 * mm, bottomMargin=MARGEM_INFERIOR,
            title=f'Orçamento {pedido.numero:06d}', author=str(pedido.filial),
        )
        def frame(topo):
            return Frame(
                MARGEM, MARGEM_INFERIOR, LARGURA_UTIL,
                A4[1] - topo - MARGEM_INFERIOR,
                leftPadding=0, rightPadding=0,
            )

        doc.addPageTemplates([
            PageTemplate(
                id='primeira', frames=[frame(10 * mm)], onPage=cls._rodape,
                autoNextPageTemplate='continuacao',
            ),
            PageTemplate(
                id='continuacao', frames=[frame(TOPO_CONTINUACAO)],
                onPage=cls._pagina_continuacao(pedido),
            ),
        ])
        e = _estilos()
        elementos = cls._empresa(pedido, e) + cls._cliente(pedido, e)
        anexos = PedidoPdfService._artes_do_pedido(
            pedido, e, LARGURA_UTIL, cor=MARINHO, cor_clara=FUNDO,
            arredondada=True,
        )
        fechamento = cls._resumo_comercial(pedido, e)
        medidor = Canvas(BytesIO())
        altura = lambda blocos: sum(b.wrapOn(medidor, LARGURA_UTIL, 100000)[1] for b in blocos)
        disponivel = A4[1] - 10 * mm - MARGEM_INFERIOR - 12 - altura(elementos)
        itens = cls._itens(pedido, e)
        if itens:
            elementos += cls._paginar_produtos(
                itens[0], disponivel,
                altura(fechamento[0]._content) + 9 if not anexos else 0, medidor,
            )
        elementos += anexos + [Spacer(1, 9)] + fechamento
        doc.build(elementos)
        return buffer.getvalue()

    @classmethod
    def _paginar_produtos(cls, tabela, disponivel, altura_fechamento, medidor):
        """Leva o último produto ao fechamento, evitando uma página só de totais."""
        blocos = []
        while tabela is not None:
            _, altura = tabela.wrapOn(medidor, LARGURA_UTIL, disponivel)
            if altura <= disponivel:
                altura_ultimo = tabela._rowHeights[0] + tabela._rowHeights[-1]
                if (altura + altura_fechamento > disponivel
                        and altura_ultimo + altura_fechamento <= ALTURA_CONTINUACAO):
                    if len(tabela._cellvalues) > 2:
                        blocos.append(cls._tabela_produtos(tabela._cellvalues[:-1]))
                        blocos.append(PageBreak())
                        tabela = cls._tabela_produtos([
                            tabela._cellvalues[0], tabela._cellvalues[-1],
                        ])
                blocos.append(tabela)
                break
            partes = tabela.splitOn(medidor, LARGURA_UTIL, disponivel)
            if not partes:
                if disponivel >= ALTURA_CONTINUACAO:
                    raise ValueError('Produto não pôde ser paginado no orçamento.')
                blocos.append(PageBreak())
            else:
                blocos += [partes[0], PageBreak()]
                tabela = partes[1]
            disponivel = ALTURA_CONTINUACAO
        return blocos

    @classmethod
    def _pagina_continuacao(cls, pedido):
        def desenhar(canvas, doc):
            cls._rodape(canvas, doc)
            e = _estilos()
            estilo = ParagraphStyle('orc_continuacao', parent=e['pequeno'], fontSize=7, leading=9)
            direita = ParagraphStyle('orc_cont_cliente', parent=estilo, alignment=2)
            cabecalho = Table([[
                Paragraph('<b>ORÇAMENTO - CONTINUAÇÃO</b>', estilo),
                Paragraph(
                    f'Clientes: {_texto(" / ".join(
                        cliente.razao_social for cliente in
                        [pedido.cliente, *pedido.clientes_adicionais.all()]
                    ))} - '
                    f'Emissão: {pedido.data_pedido:%d/%m/%Y}', direita,
                ),
            ]], colWidths=[LARGURA_UTIL * .40, LARGURA_UTIL * .60])
            cabecalho.setStyle(TableStyle(_estilo_tabela(0)))
            cabecalho.wrapOn(canvas, LARGURA_UTIL, 30)
            cabecalho.drawOn(canvas, MARGEM, A4[1] - 18 * mm)
            canvas.saveState()
            canvas.setStrokeColor(BORDA)
            canvas.line(MARGEM, A4[1] - 19 * mm, A4[0] - MARGEM, A4[1] - 19 * mm)
            canvas.restoreState()
        return desenhar

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
        marca = logo(filial, 38 * mm, 21 * mm)
        direita = [
            Paragraph('ORÇAMENTO', e['titulo']), Spacer(1, 3),
            HRFlowable(width='100%', color=MARINHO, thickness=1.2),
            Spacer(1, 5),
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
        return [tabela, Spacer(1, 6)]

    @staticmethod
    def _cliente(pedido, e):
        clientes = [pedido.cliente, *pedido.clientes_adicionais.all()]
        plural = len(clientes) > 1
        nomes = '<br/>'.join(
            _texto(getattr(cliente, 'razao_social', None) or str(cliente))
            for cliente in clientes
        )
        documentos = '<br/>'.join(
            _texto(getattr(cliente, 'cpf_cnpj', '')) or 'Não informado'
            for cliente in clientes
        )
        responsavel = (pedido.contato_nome or '').strip()
        celulas = [Paragraph(
            f'<b>{"Clientes" if plural else "Cliente"}:</b><br/>{nomes}', e['normal'],
        )]
        if responsavel:
            celulas.append(Paragraph(
                f'<b>Responsável:</b><br/>{_texto(responsavel)}', e['normal'],
            ))
            larguras = [.30, .25, .23, .22]
        else:
            larguras = [.43, .30, .27]
        celulas += [
            Paragraph(f'<b>CNPJ/CPF:</b><br/>{documentos}', e['normal']),
            Paragraph(f'<b>Data de Emissão:</b><br/>{pedido.data_pedido:%d/%m/%Y}', e['normal']),
        ]
        tabela = Table(
            [celulas],
            colWidths=[LARGURA_UTIL * largura for largura in larguras],
            cornerRadii=[6] * 4,
        )
        tabela.setStyle(TableStyle(_estilo_tabela(6) + [
            ('BACKGROUND', (0, 0), (-1, -1), FUNDO),
        ]))
        return [tabela, Spacer(1, 6)]

    @staticmethod
    def _especificacoes(item):
        """Não trunca a estrutura: cada opção comercial aparece em sua linha."""
        return especificacoes_orcamento_item(item)

    @staticmethod
    def _fotos(item, e, altura=22 * mm):
        fotos = []
        for visual in item.visuais.all():
            campo = visual.imagem or (
                visual.mockup.imagem if visual.mockup_id else None
            )
            imagem = _imagem(campo, 23 * mm, altura)
            if imagem is not None:
                fotos += [imagem, Spacer(1, 3)]
            elif campo:
                fotos.append(Paragraph('Imagem indisponível', e['pequeno']))
        for p in item.personalizacoes.all():
            if p.arquivo and p.extensao in DESENHAVEIS:
                imagem = _imagem(p.arquivo, 23 * mm, 20 * mm)
                fotos += [
                    imagem or Paragraph('Arte indisponível', e['pequeno']),
                    Spacer(1, 3),
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
            Spacer(1, 3),
            HRFlowable(width='100%', thickness=.5, color=BORDA),
            Spacer(1, 3),
            Paragraph(f'<b>Personalização por pessoa - {len(pessoas)}:</b>', e['celula']),
            Spacer(1, 3), tabela,
        ]

    @classmethod
    def _produto(cls, item, e):
        largura = cls.LARGURAS[1] - 12
        conteudo = [
            Paragraph(_texto(item.nome_exibicao), e['nome']), Spacer(1, 3),
        ]
        especificacoes = cls._especificacoes(item)
        if especificacoes:
            celulas = [
                Paragraph(f'<b>{_texto(rotulo)}:</b> {_texto(valor)}', e['celula'])
                for rotulo, valor in especificacoes
            ]
            linhas = [celulas[indice:indice + 3] for indice in range(0, len(celulas), 3)]
            while len(linhas[-1]) < 3:
                linhas[-1].append('')
            tabela_especificacoes = Table(
                linhas, colWidths=[largura / 3] * 3, hAlign='LEFT',
            )
            tabela_especificacoes.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ]))
            conteudo.append(tabela_especificacoes)
        grade = [g for g in item.grade.all() if g.quantidade]
        if grade:
            resumo = ' | '.join(f'{g.tamanho.sigla} {g.quantidade}' for g in grade)
            conteudo += [
                Spacer(1, 3),
                HRFlowable(width='100%', thickness=.5, color=BORDA),
                Spacer(1, 3),
                Paragraph(
                    f'<b>Grade:</b> {_texto(resumo)} | '
                    f'<b>Total {sum(g.quantidade for g in grade)}</b>', e['celula'],
                ),
            ]
        conteudo += cls._pessoas(item, e, largura)
        return [
            cls._fotos(item, e, (22 if especificacoes or grade else 12) * mm), conteudo,
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
        return [cls._tabela_produtos(dados)]

    @classmethod
    def _tabela_produtos(cls, dados):
        # Divide dentro da linha apenas quando um produto excede uma página.
        tabela = _TabelaProdutos(
            dados, colWidths=cls.LARGURAS, repeatRows=1,
            splitByRow=1, splitInRow=0, cornerRadii=[5] * 4,
        )
        tabela.setStyle(TableStyle(_estilo_tabela(4) + [
            ('SPAN', (0, 0), (1, 0)),
            ('BACKGROUND', (0, 0), (-1, 0), MARINHO),
            ('BOX', (0, 0), (-1, -1), .5, BORDA),
            ('LINEBELOW', (0, 1), (-1, -1), .5, BORDA),
            ('TOPPADDING', (0, 1), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ]))
        return tabela

    @staticmethod
    def _par_cartoes(esquerda, direita, largura_esquerda, altura_minima=0,
                     fundo_esquerda=FUNDO, fundo_direita=colors.white,
                     padding=8, intervalo=8):
        """Cartões de mesma altura, com respiro e bordas independentes."""
        larguras = [largura_esquerda, LARGURA_UTIL - largura_esquerda - intervalo]
        medidor = Canvas(BytesIO())

        def cartao(conteudo, largura, fundo, altura=None):
            tabela = Table([[conteudo]], colWidths=[largura],
                           rowHeights=[altura] if altura else None, cornerRadii=[7] * 4)
            tabela.setStyle(TableStyle(_estilo_tabela(padding) + [
                ('BACKGROUND', (0, 0), (-1, -1), fundo),
                ('BOX', (0, 0), (-1, -1), .6, fundo if fundo == MARINHO else BORDA),
            ]))
            return tabela

        conteudos = [esquerda, direita]
        fundos = [fundo_esquerda, fundo_direita]
        altura = max([altura_minima] + [
            cartao(c, l, f).wrapOn(medidor, l, 10000)[1]
            for c, l, f in zip(conteudos, larguras, fundos)
        ])
        if altura > ALTURA_CONTINUACAO:
            # Conteúdo excepcional não cabe em cartões lado a lado:
            # permite continuar o texto integral sem fonte minúscula.
            tabela = Table([[esquerda], [direita]], colWidths=[LARGURA_UTIL], splitInRow=1)
            tabela.setStyle(TableStyle(_estilo_tabela(padding) + [
                ('BACKGROUND', (0, 0), (0, 0), fundo_esquerda),
                ('BACKGROUND', (0, 1), (0, 1), fundo_direita),
                ('BOX', (0, 0), (-1, -1), .6, BORDA),
            ]))
            return tabela
        tabela = Table([[
            cartao(esquerda, larguras[0], fundos[0], altura), '',
            cartao(direita, larguras[1], fundos[1], altura),
        ]], colWidths=[larguras[0], intervalo, larguras[1]])
        tabela.setStyle(TableStyle(_estilo_tabela(0)))
        return tabela

    @classmethod
    def _totais(cls, pedido, e):
        detalhes = []
        for rotulo, valor in (
            ('Desconto', pedido.desconto), ('Acréscimo', pedido.acrescimo),
            ('Frete', pedido.frete),
        ):
            if valor:
                detalhes.append(f'{rotulo}: {brl(valor)}')
        total_estilo = ParagraphStyle(
            'orc_total', parent=e['nome'], fontSize=22, leading=25,
            textColor=colors.white, alignment=2,
        )
        conteudo = [
            Paragraph('<font color="white"><b>TOTAL DO ORÇAMENTO</b></font>', e['pequeno']),
            Spacer(1, 5), Paragraph(brl(pedido.valor_total), total_estilo),
        ]
        if detalhes:
            conteudo += [Spacer(1, 3), Paragraph(
                f'<font color="white">Subtotal: {brl(pedido.subtotal)} · '
                f'{_texto(" · ".join(detalhes))}</font>', e['pequeno'],
            )]
        if pedido.entrada:
            conteudo += [
                Spacer(1, 3),
                Paragraph(f'<font color="white">Entrada: {brl(pedido.entrada)} - '
                          f'Saldo: {brl(pedido.saldo)}</font>', e['pequeno']),
            ]
        return conteudo

    @classmethod
    def _pagamento_previsto(cls, pedido, e, largura=LARGURA_UTIL, compacto=False):
        linhas = list(pedido.previsao_pagamento or [])
        if not linhas:
            return [Paragraph(
                'Não informada.' if compacto else
                '<b>Forma de pagamento prevista:</b> Não informada.', e['normal'],
            )]
        rotulos = dict(pedido.FormaPagamentoPrevista.choices)
        dados = [] if compacto else [[
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
                Paragraph(
                    '' if linha.get('forma') == 'nao_informado' else brl(valor),
                    e['td_dir'],
                ),
            ])
        tabela = Table(
            dados, colWidths=[largura * .65, largura * .35],
            repeatRows=0 if compacto else 1, cornerRadii=[5] * 4,
        )
        tabela.setStyle(TableStyle(_estilo_tabela(0 if compacto else 5) + ([] if compacto else [
            ('BACKGROUND', (0, 0), (-1, 0), MARINHO),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, FUNDO]),
            ('BOX', (0, 0), (-1, -1), .5, BORDA),
        ])))
        return [tabela]

    @classmethod
    def _resumo_comercial(cls, pedido, e):
        """Fechamento compacto como no modelo comercial de uma página."""
        titulo = ParagraphStyle('orc_fechamento', parent=e['nome'], fontSize=9, leading=11)
        largura_pagamento = LARGURA_UTIL * .56
        pagamento = [Paragraph('Forma de pagamento prevista', e['secao']), Spacer(1, 4)]
        pagamento += cls._pagamento_previsto(
            pedido, e, largura_pagamento - 16, compacto=True,
        )
        pagamento += [Spacer(1, 5)] + cls._previsao_entrega(pedido, e)
        blocos = [
            Paragraph('FECHAMENTO DO ORÇAMENTO', titulo), Spacer(1, 3),
            HRFlowable(width='100%', color=MARINHO, thickness=1), Spacer(1, 6),
            cls._par_cartoes(
                pagamento, cls._totais(pedido, e), largura_pagamento,
                fundo_esquerda=colors.white, fundo_direita=MARINHO,
                padding=7, intervalo=7,
            ),
            Spacer(1, 6),
        ] + cls._fechamento(pedido, e)
        return [KeepTogether(blocos)]

    @staticmethod
    def _observacoes(pedido, e, largura=LARGURA_UTIL):
        textos = observacoes_orcamento(pedido)
        textos.append(OBSERVACAO_PAGAMENTO_ORCAMENTO)
        dados = [[_NumeroObservacao(i), Paragraph(_texto(texto), e['normal'])]
                 for i, texto in enumerate(textos, 1)]
        tabela = Table(dados, colWidths=[25, largura - 25], splitInRow=1)
        tabela.setStyle(TableStyle(_estilo_tabela(0) + [
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        return [Paragraph('Observações e prazos', e['secao']), Spacer(1, 5), tabela]

    @staticmethod
    def _previsao_entrega(pedido, e):
        data = pedido.data_prevista_entrega
        return [
            Paragraph('Previsão de entrega:', e['secao']), Spacer(1, 5),
            Paragraph(f'<b>{data:%d/%m/%Y}</b>' if data else 'A combinar',
                      ParagraphStyle('orc_entrega', parent=e['normal'], fontSize=12, leading=16)),
        ]

    @classmethod
    def _fechamento(cls, pedido, e):
        observacoes = cls._observacoes(pedido, e, LARGURA_UTIL - 16)
        quadro = Table([[observacoes]], colWidths=[LARGURA_UTIL], cornerRadii=[5] * 4)
        quadro.setStyle(TableStyle(_estilo_tabela(8) + [
            ('BACKGROUND', (0, 0), (-1, -1), FUNDO),
            ('BOX', (0, 0), (-1, -1), .5, BORDA),
        ]))
        if quadro.wrapOn(Canvas(BytesIO()), LARGURA_UTIL, 100000)[1] > ALTURA_CONTINUACAO:
            # Observações excepcionais precisam continuar em outras páginas.
            # Sem o cartão externo, a tabela interna consegue se dividir por linha.
            return observacoes
        return [quadro]
