"""
PDF do pedido de produção — a ficha que vai para o cliente e para a fábrica.

GERADO SOB DEMANDA, nunca gravado. Um arquivo salvo no momento em que o
pedido é fechado envelhece na primeira alteração de grade, arte ou valor, e
aí passam a existir duas versões do mesmo pedido: a do papel e a da tela.
Como a URL é estável, o link continua valendo — o que muda é que ele sempre
devolve o pedido de agora.

O documento monta só as seções que têm conteúdo. Um pedido sem
personalização individual não ganha uma página em branco intitulada
"Personalização" — seção vazia num documento que vai para o cliente parece
erro de sistema.
"""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO

from django.urls import reverse
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    HRFlowable, Image, KeepInFrame, KeepTogether, Paragraph, SimpleDocTemplate,
    PageBreak, Spacer, Table, TableStyle,
)

from .pdf_marca import (
    ALTURA_TARJA, bloco_empresa, desenhar_tarja, esc, estilos_empresa, logo,
)
from .item_groups import agrupar_itens_op

AZUL = colors.HexColor('#0f4aa1')
AZUL_ESCURO = colors.HexColor('#173f7f')
AZUL_CLARO = colors.HexColor('#eef3f8')
CINZA = colors.HexColor('#64748b')
BORDA = colors.HexColor('#cbd5e1')
FUNDO = colors.HexColor('#f8fafc')
TEXTO = colors.HexColor('#111827')
PAGINA = landscape(A4)
MARGEM = 9 * mm
LARGURA_UTIL = PAGINA[0] - 2 * MARGEM

# O QUE O PDF CONSEGUE DESENHAR. Não é a mesma lista de `pode_pre_visualizar`,
# que é do NAVEGADOR: o SVG entra lá e não aqui, porque o `Image` do reportlab
# lê bitmap pelo PIL. Sem esta lista o SVG caía na exceção de `_imagem` e
# sumia -- funcionava, mas por acidente, e acidente não avisa quando muda.
DESENHAVEIS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def _estilos():
    base = getSampleStyleSheet()
    return {
        **estilos_empresa(),
        'titulo': ParagraphStyle('t', parent=base['Title'], fontSize=15, spaceAfter=2),
        'secao': ParagraphStyle(
            's', parent=base['Heading2'], fontName='Helvetica-Bold',
            fontSize=8.4, leading=10, textColor=AZUL_ESCURO,
        ),
        'secao_numero': ParagraphStyle(
            'sn', parent=base['Normal'], fontName='Helvetica-Bold',
            fontSize=9, leading=10, textColor=colors.white, alignment=1,
        ),
        'normal': ParagraphStyle(
            'n', parent=base['Normal'], fontSize=7.2, leading=9, textColor=TEXTO,
        ),
        'pequeno': ParagraphStyle(
            'p', parent=base['Normal'], fontSize=6.5, leading=8, textColor=CINZA,
        ),
        'observacao_produto': ParagraphStyle(
            'op', parent=base['Normal'], fontName='Helvetica-Bold',
            fontSize=7.8, leading=9.5, textColor=colors.black,
        ),
        'celula': ParagraphStyle(
            'c', parent=base['Normal'], fontSize=7.1, leading=8.6, textColor=TEXTO,
        ),
    }


def _tabela(
    dados, larguras, cabecalho=True, cor_cabecalho=AZUL,
    fundo_linha=FUNDO, arredondada=False, padding_vertical=3,
):
    """Tabela no padrão já usado nos outros PDFs do sistema."""
    estilo = [
        ('GRID', (0, 0), (-1, -1), 0.35, BORDA),
        ('FONTSIZE', (0, 0), (-1, -1), 7.1),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), padding_vertical),
        ('BOTTOMPADDING', (0, 0), (-1, -1), padding_vertical),
    ]
    if cabecalho:
        estilo += [
            ('BACKGROUND', (0, 0), (-1, 0), cor_cabecalho),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, fundo_linha]),
        ]
    elif dados and len(dados[0]) == 4:
        estilo += [
            ('BACKGROUND', (0, 0), (0, -1), FUNDO),
            ('BACKGROUND', (2, 0), (2, -1), FUNDO),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ]
    tabela = Table(
        dados, colWidths=larguras, repeatRows=1 if cabecalho else 0,
        cornerRadii=[7, 7, 7, 7] if arredondada else None,
    )
    tabela.setStyle(TableStyle(estilo))
    return tabela


def _estilos_leitura(e, fator=1):
    """Fonte de leitura primeiro; compactação só depois de medir a página."""
    resultado = dict(e)
    for chave, fonte in (
        ('normal', 9.5), ('celula', 9.5), ('pequeno', 8.5),
        ('observacao_produto', 9.5),
    ):
        resultado[chave] = ParagraphStyle(
            f'{chave}_leitura_{fator}', parent=e[chave],
            fontSize=fonte * fator, leading=fonte * 1.22 * fator,
        )
    resultado['pessoa_compacta'] = resultado['celula']
    # Faixas menores deixam a altura útil para os dados, não para títulos.
    resultado['altura_secao'] = 5 * mm
    resultado['padding_tabela'] = 1.2
    resultado['padding_observacao'] = 2
    return resultado


def _barra_secao(
    numero, titulo, e, largura_util, cor=AZUL, cor_clara=AZUL_CLARO,
    arredondada=False,
):
    """Faixa compacta usada como hierarquia visual em cada bloco da OP."""
    titulo = Paragraph(esc(titulo), e['secao'])
    largura_titulo = largura_util - (10 * mm if numero is not None else 0) - 9
    altura_titulo = max(e.get('altura_secao', 9 * mm), titulo.wrap(largura_titulo, 1000)[1] + 2)
    if numero is None:
        tabela = Table(
            [[titulo]], colWidths=[largura_util], rowHeights=[altura_titulo],
            cornerRadii=[7, 7, 7, 7] if arredondada else None,
        )
        estilo = [
            ('BACKGROUND', (0, 0), (-1, -1), cor_clara),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ]
    else:
        tabela = Table(
            [[Paragraph(f'{int(numero):02d}', e['secao_numero']), titulo]],
            colWidths=[10 * mm, largura_util - 10 * mm], rowHeights=[altura_titulo],
            cornerRadii=[7, 7, 7, 7] if arredondada else None,
        )
        estilo = [
            ('BACKGROUND', (0, 0), (0, 0), cor),
            ('BACKGROUND', (1, 0), (1, 0), cor_clara),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (0, 0), 0),
            ('RIGHTPADDING', (0, 0), (0, 0), 0),
            ('LEFTPADDING', (1, 0), (1, 0), 5),
            ('RIGHTPADDING', (1, 0), (1, 0), 4),
        ]
    tabela.setStyle(TableStyle(estilo + [
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
    ]))
    return tabela


def _imagem(campo, largura, altura):
    """
    Imagem do storage, ou None quando o arquivo não abre.

    Arquivo apagado do disco ou storage remoto fora do ar não pode derrubar
    a geração do pedido inteiro: o documento sai sem a foto, que é bem
    melhor do que não sair.
    """
    if not campo:
        return None
    try:
        campo.open('rb')
        dados = BytesIO(campo.read())
        campo.close()
        return Image(dados, width=largura, height=altura, kind='proportional')
    except Exception:
        return None


def _estrutura_item(item):
    """Separa observação livre dos campos preenchidos da estrutura da peça."""
    texto = (item.observacoes or '').strip()
    marcador = 'Estrutura da peça:'
    if marcador not in texto:
        return texto, []
    livre, bloco = texto.split(marcador, 1)
    campos = []
    for linha in bloco.splitlines():
        linha = linha.strip()
        if not linha or ':' not in linha:
            continue
        rotulo, valor = (parte.strip() for parte in linha.split(':', 1))
        if valor:
            campos.append((rotulo, valor))
    return livre.strip(), campos


def whatsapp_numero(pedido) -> str:
    """
    Número do cliente em formato aceito pelo wa.me: só dígitos, com DDI.

    O 55 é acrescentado quando o cadastro tem só DDD + número, que é como
    quase todo telefone brasileiro é digitado. Número que já vem com DDI
    (13 dígitos) passa intacto — reescrevê-lo transformaria um celular certo
    num errado.
    """
    bruto = (
        getattr(pedido, 'contato_telefone', '')
        or getattr(pedido.cliente, 'celular', '')
        or getattr(pedido.cliente, 'telefone', '')
        or ''
    )
    digitos = ''.join(c for c in bruto if c.isdigit())
    if not digitos:
        return ''
    if len(digitos) in (10, 11):
        return f'55{digitos}'
    return digitos


def mensagem_whatsapp(pedido, link: str) -> str:
    """
    A mensagem padrão da especificação, com o link do pedido no lugar do anexo.

    O texto original dizia "em anexo". O wa.me — que é o que abre o WhatsApp
    a partir do navegador — só carrega TEXTO: não existe forma de anexar
    arquivo por link. Prometer anexo e mandar só texto faria o cliente
    procurar um arquivo que não chegou, então a frase virou o link. Anexar
    de verdade continua possível pelo botão de baixar, arrastando o arquivo
    na conversa.

    O link é o da PÁGINA do pedido, não o do PDF: no celular o PDF abre no
    visualizador e o status do pedido fica de fora — e é o status que o
    cliente volta para consultar depois. O PDF está a um toque de lá.
    """
    entrega = (
        f'{pedido.data_prevista_entrega:%d/%m/%Y}'
        if pedido.data_prevista_entrega else 'a combinar'
    )
    cliente = (
        getattr(pedido.cliente, 'nome_fantasia', '')
        or getattr(pedido.cliente, 'razao_social', '')
        or 'cliente'
    )
    return (
        f'Olá, {cliente}!\n\n'
        f'Seu pedido #{pedido.numero:06d} foi finalizado.\n\n'
        f'Neste link você acompanha o pedido e baixa o PDF:\n'
        f'{link}\n\n'
        f'Prazo de entrega: {entrega}.\n\n'
        f'Obrigado!'
    )


def _destino_qr(pedido, base_url: str = '') -> str:
    """A folha impressa leva a equipe para a conferência de entrega da OP."""
    caminho = reverse('moda:pedido-conferencia', args=[pedido.pk])
    return f'{base_url.rstrip("/")}{caminho}' if base_url else caminho


class PedidoPdfService:

    @classmethod
    def gerar(cls, pedido, base_url: str = '') -> bytes:
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=PAGINA,
            leftMargin=MARGEM, rightMargin=MARGEM,
            topMargin=31 * mm, bottomMargin=9 * mm,
            title=f'Pedido {pedido.numero:06d}',
            author=str(pedido.filial),
        )
        e = _estilos()
        elementos = []
        itens = list(pedido.itens.all())
        grupos = agrupar_itens_op(itens)
        # O frame do ReportLab desconta 6 pt em cada borda além das margens.
        altura_util = PAGINA[1] - doc.topMargin - doc.bottomMargin - 14
        medidor = Canvas(BytesIO())
        if not itens:
            blocos = cls._cliente(pedido, e)
            blocos += cls._observacoes_op(pedido, e, LARGURA_UTIL)
            elementos.append(KeepInFrame(LARGURA_UTIL - 12, altura_util, blocos, mode='shrink'))
        else:
            for indice, grupo in enumerate(grupos):
                if len(grupo.itens) > 1:
                    elementos.append(cls._pagina_grupo(
                        pedido, grupo, e, LARGURA_UTIL - 12, altura_util,
                        incluir_observacoes=indice == 0,
                    ))
                    if indice < len(grupos) - 1:
                        elementos.append(PageBreak())
                    continue
                meia = (LARGURA_UTIL - 6 * mm) / 2
                esquerda = cls._coluna_producao(
                    pedido, grupo.itens[0], e, meia, altura_util, medidor,
                )
                # A imagem depende apenas do conteúdo da DIREITA. A lista
                # de pessoas não disputa mais altura com a arte.
                complemento = []
                if indice == 0:
                    complemento += cls._observacoes_op(pedido, e, meia)
                complemento_frame = KeepInFrame(
                    meia, altura_util * .70, complemento, mode='shrink',
                )
                _, altura_complemento = complemento_frame.wrapOn(medidor, meia, altura_util)
                altura_arte = altura_util - altura_complemento - 4
                direita = cls._arte(
                    grupo.itens[0], e, meia, altura_maxima=altura_arte,
                )
                if not direita:
                    direita = [
                        _barra_secao(4, f'IMAGENS E IMPRESSÃO - {grupo.nome}', e, meia),
                        Spacer(1, 4),
                        Paragraph('Nenhuma imagem anexada.', e['pequeno']),
                    ]
                arte = KeepInFrame(
                    meia, altura_arte, direita, mode='shrink',
                )
                direita = [arte, complemento_frame]
                pagina = Table([[
                    KeepInFrame(meia, altura_util, esquerda, mode='error'),
                    KeepInFrame(meia, altura_util, direita, mode='error'),
                ]], colWidths=[meia, meia], rowHeights=[altura_util])
                pagina.setStyle(TableStyle([
                    ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                    # A altura dos dois lados já foi calculada contra
                    # ``altura_util``. O padding vertical padrão da Table (3 pt
                    # em cima e embaixo) reduzia a célula depois dessa medição e
                    # fazia OPs quase cheias estourarem com LayoutError.
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                    ('LEFTPADDING', (0, 0), (0, 0), 0),
                    ('RIGHTPADDING', (0, 0), (0, 0), 3 * mm),
                    ('LEFTPADDING', (1, 0), (1, 0), 3 * mm),
                    ('RIGHTPADDING', (1, 0), (1, 0), 0),
                    ('LINEBEFORE', (1, 0), (1, 0), .5, BORDA),
                ]))
                elementos.append(pagina)
                if indice < len(grupos) - 1:
                    elementos.append(PageBreak())

        cabecalho = cls._cabecalho_folha(pedido, base_url)
        doc.build(elementos, onFirstPage=cabecalho, onLaterPages=cabecalho)
        return buffer.getvalue()

    @classmethod
    def _coluna_producao(cls, pedido, item, e, largura, altura, medidor):
        """Reserva espaço para todos os nomes, sem encolher a coluna da arte."""
        pessoas = list(item.individuais.all())
        e = _estilos_leitura(e)
        produto = cls._cliente(pedido, e, largura)
        produto += cls._item(pedido, item, e, 0, largura_util=largura)
        altura_produto_maxima = altura
        if pessoas:
            alturas_listas = []
            for colunas in range(1, min(3, len(pessoas)) + 1):
                lista = cls._personalizacao_item(item, e, largura, pessoas=pessoas, colunas=colunas)
                alturas_listas.append(sum(bloco.wrapOn(medidor, largura, altura)[1] for bloco in lista))
            altura_produto_maxima = max(altura * .35, altura - min(alturas_listas) - 2)
        produto_frame = KeepInFrame(
            largura, altura_produto_maxima, produto, mode='shrink',
        )
        _, altura_produto = produto_frame.wrapOn(medidor, largura, altura)
        if not pessoas:
            return [produto_frame]

        altura_lista = altura - altura_produto - 2
        candidatos = []
        for colunas in range(1, min(3, len(pessoas)) + 1):
            def montar(fator):
                lista = cls._personalizacao_item(
                    item, e, largura, pessoas=pessoas, colunas=colunas,
                    fator_fonte=fator,
                )
                frame = KeepInFrame(largura, altura_lista, lista, mode='shrink')
                frame.wrapOn(medidor, largura, altura_lista)
                return frame

            fator = 1
            frame = montar(fator)
            if getattr(frame, '_scale', 1) > 1:
                # Compactar texto/padding antes de reduzir o bloco inteiro
                # mantém a tabela alinhada à largura da coluna esquerda.
                baixo, alto = .25, 1
                fator, frame = baixo, montar(baixo)
                for _ in range(7):
                    meio = (baixo + alto) / 2
                    tentativa = montar(meio)
                    if getattr(tentativa, '_scale', 1) <= 1:
                        baixo = meio
                        fator, frame = meio, tentativa
                    else:
                        alto = meio
            fonte = e['celula'].fontSize
            # Nomes longos podem ficar melhores em duas colunas que em
            # três. Escolhemos pela fonte efetiva depois da medição real,
            # não só pela quantidade de pessoas. Empate: menos colunas.
            legibilidade = fonte * fator / getattr(frame, '_scale', 1)
            candidatos.append((legibilidade, -colunas, frame))
        return [produto_frame, max(candidatos, key=lambda c: c[:2])[2]]

    @classmethod
    def _informacoes_grupo(cls, pedido, grupo, e, largura, altura, incluir_observacoes):
        """Compara uma, duas e três colunas sem encolher a largura da ficha."""
        pessoas_por_item = [(item, list(item.individuais.all())) for item in grupo.itens]
        medidor = Canvas(BytesIO())

        def montar(colunas, fator):
            leitura = _estilos_leitura(e, fator)
            blocos = cls._cliente(pedido, leitura, largura)
            blocos += cls._item(
                pedido, grupo.itens[0], leitura, 0, largura_util=largura,
                titulo=f'PRODUTO - {grupo.nome}', incluir_grade=False,
                excluir_campos=('tipo impressão', 'tipo impressao', 'tipo de impressão'),
            )
            blocos += cls._grades_grupo(grupo, leitura, largura)
            for item, pessoas in pessoas_por_item:
                if pessoas:
                    blocos += cls._personalizacao_item(
                        item, leitura, largura, pessoas=pessoas,
                        cor=colors.HexColor(item.grade_cor),
                        cor_clara=colors.HexColor(item.grade_fundo),
                        colunas=min(colunas, len(pessoas)),
                    )
            if incluir_observacoes:
                blocos += cls._observacoes_op(pedido, leitura, largura)
            frame = KeepInFrame(largura, altura, blocos, mode='shrink', hAlign='LEFT')
            frame.wrapOn(medidor, largura, altura)
            return frame

        candidatos = []
        for colunas in (1, 2, 3):
            fator = 1
            frame = montar(colunas, fator)
            if getattr(frame, '_scale', 1) > 1:
                baixo, alto = .35, 1
                fator, frame = baixo, montar(colunas, baixo)
                for _ in range(8):
                    meio = (baixo + alto) / 2
                    tentativa = montar(colunas, meio)
                    if getattr(tentativa, '_scale', 1) <= 1:
                        baixo = meio
                        fator, frame = meio, tentativa
                    else:
                        alto = meio
            candidatos.append((fator / getattr(frame, '_scale', 1), -colunas, frame))
            if fator == 1:
                break  # Já cabe com a fonte máxima e o menor número de colunas.
        return max(candidatos, key=lambda candidato: candidato[:2])[2]

    @classmethod
    def _pagina_grupo(
        cls, pedido, grupo, e, largura, altura, *, incluir_observacoes=False,
    ):
        """Informações à esquerda e imagens ocupando toda a coluna direita."""
        intervalo = 5 * mm
        largura_esquerda = (largura - intervalo) / 2
        largura_artes = largura_esquerda
        informacoes = cls._informacoes_grupo(
            pedido, grupo, e, largura_esquerda, altura, incluir_observacoes,
        )
        artes = cls._artes_grupo(grupo, e, largura_artes, altura)
        if not artes:
            artes = [Paragraph('Nenhuma imagem anexada ao produto.', e['pequeno'])]
        pagina = Table([[
            informacoes,
            KeepInFrame(largura_artes, altura, artes, mode='shrink'),
        ]], colWidths=[largura_esquerda + intervalo, largura_artes])
        pagina.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (0, 0), 0),
            ('RIGHTPADDING', (0, 0), (0, 0), 5 * mm),
            ('LEFTPADDING', (1, 0), (1, 0), 0),
            ('RIGHTPADDING', (1, 0), (1, 0), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return KeepInFrame(largura, altura, [pagina], mode='shrink', hAlign='LEFT')

    @staticmethod
    def _grades_grupo(grupo, e, largura):
        """Uma única matriz compacta para comparar todas as grades."""
        tamanhos = []
        vistos = set()
        for item in grupo.itens:
            for celula in item.grade.all():
                if celula.tamanho_id not in vistos:
                    vistos.add(celula.tamanho_id)
                    tamanhos.append(celula.tamanho)
        tamanhos.sort(key=lambda tamanho: (tamanho.ordem, tamanho.sigla))
        if not tamanhos:
            return []
        estilo_nome = ParagraphStyle(
            'nome_variante_grade', parent=e['celula'],
            fontName='Helvetica-Bold', fontSize=6.4, leading=7.5,
        )
        estilo_cabecalho = ParagraphStyle(
            'cabecalho_grade_grupo', parent=e['pequeno'],
            fontName='Helvetica-Bold', fontSize=6, leading=7,
            textColor=colors.white,
        )
        dados = [[Paragraph('VARIANTE / GRADE', estilo_cabecalho)]
                 + [Paragraph(esc(t.sigla), estilo_cabecalho) for t in tamanhos]
                 + [Paragraph('TOTAL', estilo_cabecalho)]]
        estilos = [
            ('BACKGROUND', (0, 0), (-1, 0), AZUL),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), .35, BORDA),
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), e.get('padding_tabela', 3)),
            ('BOTTOMPADDING', (0, 0), (-1, -1), e.get('padding_tabela', 3)),
        ]
        for linha, item in enumerate(grupo.itens, start=1):
            quantidades = {celula.tamanho_id: celula.quantidade for celula in item.grade.all()}
            dados.append([
                Paragraph(f'<b>{esc(item.grade_rotulo)}</b>', estilo_nome),
                *[str(quantidades.get(tamanho.pk, 0)) for tamanho in tamanhos],
                str(sum(quantidades.values())),
            ])
            estilos += [
                ('BACKGROUND', (0, linha), (-1, linha), colors.HexColor(item.grade_fundo)),
                ('TEXTCOLOR', (0, linha), (0, linha), colors.HexColor(item.grade_cor)),
                ('FONTNAME', (-1, linha), (-1, linha), 'Helvetica-Bold'),
            ]
        largura_nome = min(35 * mm, largura * .30)
        largura_numero = (largura - largura_nome) / (len(tamanhos) + 1)
        tabela = Table(
            dados, colWidths=[largura_nome] + [largura_numero] * (len(tamanhos) + 1),
            repeatRows=1,
        )
        tabela.setStyle(TableStyle(estilos))
        return [Spacer(1, 4), _barra_secao(
            3, 'GRADES', e, largura,
        ), Spacer(1, 3), tabela]

    @classmethod
    def _artes_grupo(cls, grupo, e, largura, altura):
        blocos = []
        altura_item = max(24 * mm, altura / max(1, len(grupo.itens)))
        for item in grupo.itens:
            cor = colors.HexColor(item.grade_cor)
            cor_clara = colors.HexColor(item.grade_fundo)
            blocos += cls._arte(
                item, e, largura, altura_maxima=altura_item,
                cor=cor, cor_clara=cor_clara,
                titulo=f'IMAGENS · {item.grade_rotulo}',
            )
        return blocos

    # ── Cabeçalho ────────────────────────────────────────────────────────

    @staticmethod
    def _cabecalho_folha(pedido, base_url=''):
        """Cabeçalho da OP com o QR da conferência repetido em cada folha."""
        import qrcode

        imagem = qrcode.make(_destino_qr(pedido, base_url))
        qr_buffer = BytesIO()
        imagem.save(qr_buffer, 'PNG')
        qr_buffer.seek(0)
        qr = ImageReader(qr_buffer)

        def desenhar(canvas, doc):
            canvas.saveState()
            largura, altura = canvas._pagesize
            x = MARGEM + 20 * mm

            canvas.drawImage(
                qr, MARGEM, altura - 23 * mm,
                width=16 * mm, height=16 * mm,
                preserveAspectRatio=True, mask='auto',
            )

            canvas.setFillColor(AZUL_ESCURO)
            canvas.setFont('Helvetica-Bold', 17)
            canvas.drawString(x, altura - 13 * mm, 'ORDEM DE PRODUÇÃO')

            canvas.setFont('Helvetica-Bold', 9.5)
            canvas.setFillColor(TEXTO)
            canvas.drawString(x, altura - 20 * mm, 'PEDIDO')
            canvas.setFillColor(AZUL)
            canvas.drawString(x + 17 * mm, altura - 20 * mm, f'#{pedido.numero:06d}')

            marca = logo(pedido.filial, 30 * mm, 12 * mm)
            if marca is not None:
                l, a = marca.wrapOn(canvas, 30 * mm, 12 * mm)
                marca.drawOn(
                    canvas, largura - MARGEM - l,
                    altura - 8 * mm - a,
                )

            # A previsão é uma informação de chão de fábrica: fica grande no
            # canto direito, junto da marca, para continuar visível mesmo
            # quando a OP estiver impressa em várias páginas. Sem data não
            # desenhamos rótulo nem espaço reservado.
            if pedido.data_prevista_entrega:
                canvas.setFillColor(AZUL_ESCURO)
                canvas.setFont('Helvetica-Bold', 13)
                canvas.drawRightString(
                    largura - MARGEM - 34 * mm,
                    altura - 12 * mm,
                    'ENTREGA: '
                    + pedido.data_prevista_entrega.strftime('%d/%m/%Y'),
                )

            canvas.setStrokeColor(AZUL)
            canvas.setLineWidth(1.1)
            canvas.line(MARGEM, altura - 25 * mm, largura - MARGEM, altura - 25 * mm)
            canvas.restoreState()

        return desenhar

    @staticmethod
    def _cabecalho(pedido, e) -> list:
        """
        Os dados da casa, abaixo da tarja — o MESMO bloco do orçamento.

        A marca e o título saem no canvas (`desenhar_tarja`), e não aqui: a
        faixa sangra até a borda da folha, e flowable nenhum alcança a
        margem. Aqui fica só o que corre no fluxo.
        """
        dados = [[
            Paragraph('<b>Data do pedido</b>', e['celula']),
            Paragraph('<b>Entrega prevista</b>', e['celula']),
            Paragraph('<b>Status</b>', e['celula']),
            Paragraph('<b>Prioridade</b>', e['celula']),
        ], [
            Paragraph(f'{pedido.data_pedido:%d/%m/%Y}', e['celula']),
            Paragraph(
                f'{pedido.data_prevista_entrega:%d/%m/%Y}'
                if pedido.data_prevista_entrega else '—', e['celula'],
            ),
            Paragraph(pedido.get_status_display(), e['celula']),
            Paragraph(pedido.get_prioridade_display(), e['celula']),
        ]]
        largura = LARGURA_UTIL / 4

        return [
            bloco_empresa(pedido.filial, e, LARGURA_UTIL), Spacer(1, 6),
            _tabela(dados, [largura] * 4, cabecalho=False), Spacer(1, 4),
        ]

    # ── Cliente ──────────────────────────────────────────────────────────

    @staticmethod
    def _cliente(pedido, e, largura_util=LARGURA_UTIL) -> list:
        c = pedido.cliente
        clientes = [c, *pedido.clientes_adicionais.all()]
        plural = len(clientes) > 1
        nomes = '<br/>'.join(esc(cliente.razao_social) for cliente in clientes)
        documentos = '<br/>'.join(
            esc(getattr(cliente, 'cpf_cnpj', '')) or '—' for cliente in clientes
        )
        # WhatsApp: o cadastro guarda celular, e é dele que sai o contato de
        # WhatsApp na prática. Um campo próprio não existe — dizer "celular"
        # seria mais honesto, mas o pedido pede WhatsApp e é o mesmo número.
        whatsapp = getattr(c, 'celular', '') or ''
        responsavel = (pedido.contato_nome or '').strip()
        telefone = pedido.contato_telefone or getattr(c, 'telefone', '') or ''

        if responsavel:
            dados = [[
                Paragraph(f'<b>{"Clientes" if plural else "Cliente"}</b>', e['celula']),
                Paragraph(nomes, e['celula']),
                Paragraph('<b>Responsável</b>', e['celula']),
                Paragraph(esc(responsavel), e['celula']),
            ], [
                Paragraph('<b>CPF/CNPJ</b>', e['celula']),
                Paragraph(documentos, e['celula']),
                Paragraph('<b>Telefone</b>', e['celula']),
                Paragraph(esc(telefone) or '—', e['celula']),
            ]]
        else:
            dados = [[
                Paragraph(f'<b>{"Clientes" if plural else "Cliente"}</b>', e['celula']),
                Paragraph(nomes, e['celula']),
                Paragraph('<b>CPF/CNPJ</b>', e['celula']),
                Paragraph(documentos, e['celula']),
            ]]
        if not responsavel and telefone:
            dados.append([
                Paragraph('<b>Telefone</b>', e['celula']),
                Paragraph(esc(telefone), e['celula']), '', '',
            ])
        dados.append([Paragraph('<b>WhatsApp</b>', e['celula']),
             Paragraph(esc(whatsapp) or '—', e['celula']),
             Paragraph('<b>E-mail</b>', e['celula']),
             Paragraph(esc(getattr(c, 'email', '')) or '—', e['celula'])])
        largura_rotulo = min((26 if 'padding_tabela' in e else 22) * mm, largura_util * .22)
        largura_valor = largura_util / 2 - largura_rotulo
        larguras = [largura_rotulo, largura_valor] * 2

        return [
            _barra_secao(1, 'INFORMAÇÕES DOS CLIENTES' if plural else 'INFORMAÇÕES DO CLIENTE', e, largura_util),
            Spacer(1, 3), _tabela(dados, larguras, cabecalho=False, padding_vertical=e.get('padding_tabela', 3)),
            Spacer(1, 4),
        ]

    # ── Arte do pedido ───────────────────────────────────────────────────

    @staticmethod
    def _artes_do_pedido(
        pedido, e, largura_util=LARGURA_UTIL, cor=AZUL,
        cor_clara=AZUL_CLARO, arredondada=False,
    ) -> list:
        """
        A ARTE, desenhada — logo depois do cliente, ainda na primeira página.

        Era o que faltava no documento: o pedido guardava a arte desde o
        primeiro contato, a página do link já a mostrava, e só o PDF saía sem
        ela. Quem recebia o arquivo via a ficha inteira e nenhuma imagem da
        peça que estava comprando.

        Todos os anexos entram no índice do documento. Imagens são exibidas;
        formatos que o ReportLab não desenha aparecem pelo nome para que
        nenhum arquivo anexado fique invisível na conferência.
        """
        nomes_visuais = {
            visual.imagem.name
            for item in pedido.itens.all()
            for visual in item.visuais.all()
            if visual.imagem
        }
        artes = [
            anexo for anexo in pedido.arquivos.all()
            if not anexo.arquivo or anexo.arquivo.name not in nomes_visuais
        ]
        if not artes:
            return []

        # Três por linha: menor que isso desperdiça a folha, maior que isso
        # deixa a arte pequena demais para conferir escudo e numeração.
        por_linha = 3
        largura = largura_util / por_linha
        blocos = [
            Spacer(1, 4),
            _barra_secao(
                None, 'ANEXOS COMPLEMENTARES', e, largura_util,
                cor=cor, cor_clara=cor_clara, arredondada=arredondada,
            ),
            Spacer(1, 3),
        ]

        for inicio in range(0, len(artes), por_linha):
            faixa = artes[inicio:inicio + por_linha]
            imagens, rotulos = [], []
            for arte in faixa:
                imagem = (
                    _imagem(arte.arquivo, largura - 6 * mm, 34 * mm)
                    if arte.extensao in DESENHAVEIS else None
                )
                # Arquivo que não abre vira o NOME, e não um buraco: o
                # storage pode estar fora do ar, e some a imagem, não a
                # informação de que ela existe.
                imagens.append(imagem or Paragraph(esc(arte.nome_arquivo), e['pequeno']))
                rotulos.append(Paragraph(
                    arte.descricao or arte.nome_arquivo,
                    ParagraphStyle('rot', parent=e['pequeno'], alignment=1),
                ))
            # A última linha não estica as imagens para preencher a folha.
            while len(imagens) < por_linha:
                imagens.append('')
                rotulos.append('')

            grade = Table([imagens, rotulos], colWidths=[largura] * por_linha)
            grade.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('TOPPADDING', (0, 1), (-1, 1), 3),
                ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
            ]))
            blocos.append(grade)

        return blocos

    # ── Produto, arte e grade ────────────────────────────────────────────

    @classmethod
    def _item(
        cls, pedido, item, e, indice, largura_util=LARGURA_UTIL, *,
        titulo=None, cor=AZUL, cor_clara=AZUL_CLARO, incluir_grade=True,
        excluir_campos=(),
    ) -> list:
        """
        Um produto: identificação, arte e grade.

        UM ABAIXO DO OUTRO, e não uma folha por produto. Antes cada item
        começava em página nova a partir do segundo, para a fábrica poder
        destacar uma folha por peça -- só que um pedido de três camisas
        virava três folhas quase vazias, e quem confere a ficha inteira
        passava a folhear. Agora o produto seguinte ocupa o espaço que
        sobrou.

        O bloco vai em `KeepTogether`: cabendo, o produto fica inteiro na
        mesma folha; não cabendo, desce todo para a próxima. O que não pode
        é o título numa página e a grade na outra -- quem lê no chão de
        fábrica perde a referência de qual peça está olhando.
        """
        blocos = []
        if indice:
            # Uma régua fina separa um produto do outro, já que a quebra de
            # página deixou de fazer esse papel.
            blocos.append(Spacer(1, 5))
            blocos.append(HRFlowable(
                width='100%', thickness=0.5, color=BORDA, spaceAfter=6,
            ))

        blocos.append(_barra_secao(
            2, titulo or f'PRODUTO - {item.nome_exibicao}', e, largura_util,
            cor=cor, cor_clara=cor_clara,
        ))
        blocos.append(Spacer(1, 3))

        observacao, estrutura = _estrutura_item(item)
        nome_produto = item.nome_base_op if titulo else item.nome_exibicao
        campos = [('Produto', nome_produto)]
        campos += [
            (rotulo, valor) for rotulo, valor in (
                ('Modelo', str(item.modelo) if item.modelo_id else ''),
                ('Cor', str(item.cor) if item.cor_id else ''),
                ('Tecido / Malha', item.tecido_exibicao),
                ('Gola', item.get_gola_display() if item.gola else ''),
                ('Manga', item.get_manga_display() if item.manga else ''),
                ('Acabamento', item.acabamento),
            ) if valor
        ]
        # A estrutura é preenchida especificamente para esta OP e por isso
        # vence o valor legado do cadastro quando os rótulos coincidem.
        indices = {rotulo.casefold(): indice for indice, (rotulo, _) in enumerate(campos)}
        excluir = {rotulo.casefold() for rotulo in excluir_campos}
        for par in estrutura:
            chave = par[0].casefold()
            if chave in excluir:
                continue
            if chave in indices:
                campos[indices[chave]] = par
            else:
                indices[chave] = len(campos)
                campos.append(par)
        pares_por_linha = 3 if largura_util >= 200 * mm else 2
        dados = []
        for inicio in range(0, len(campos), pares_por_linha):
            linha = []
            for rotulo, valor in campos[inicio:inicio + pares_por_linha]:
                linha += [Paragraph(f'<b>{esc(rotulo)}</b>', e['pequeno']),
                          Paragraph(esc(valor), e['celula'])]
            while len(linha) < pares_por_linha * 2:
                linha += ['', '']
            dados.append(linha)
        largura_rotulo = (26 if 'padding_tabela' in e else 22) * mm
        blocos.append(_tabela(
            dados,
            [largura_rotulo, largura_util / pares_por_linha - largura_rotulo]
            * pares_por_linha,
            cabecalho=False,
            padding_vertical=e.get('padding_tabela', 3),
        ))

        if item.eh_conjunto:
            blocos += cls._componentes_conjunto(item, e, largura_util)

        if observacao:
            blocos += cls._quadro_observacoes(
                'OBSERVAÇÕES DO PRODUTO', observacao, e, largura_util,
                estilo_texto=e['observacao_produto'],
            )

        if incluir_grade and not item.eh_conjunto:
            blocos += cls._grade(
                item, e, largura_util, cor=cor, cor_clara=cor_clara,
            )
        # Na ficha horizontal, deixar os sub-blocos fluírem aproveita o
        # restante da página. O KeepTogether empurrava o produto inteiro
        # para outra folha mesmo quando ainda havia bastante espaço.
        return blocos

    @staticmethod
    def _componentes_conjunto(item, e, largura_util):
        blocos = []
        for componente in item.componentes_conjunto:
            linhas_grade = []
            for grade in componente['grades']:
                resumo = ' | '.join(
                    f'{tamanho["sigla"]} {tamanho["quantidade"]}'
                    for tamanho in grade['tamanhos']
                )
                linhas_grade.append(f'{grade["nome"]}: {resumo}')
            especificacoes = ' · '.join(
                f'{rotulo}: {valor}' for rotulo, valor in componente['estrutura']
            ) or 'Sem especificações adicionais'
            dados = [[
                Paragraph(f'<b>{esc(componente["label"])}</b>', e['celula']),
                Paragraph(esc(especificacoes), e['celula']),
                Paragraph(esc(' / '.join(linhas_grade)), e['celula']),
                Paragraph(f'<b>{componente["total"]}</b>', e['celula']),
            ]]
            tabela = _tabela(
                dados,
                [largura_util * .16, largura_util * .42, largura_util * .32, largura_util * .10],
                cabecalho=False,
            )
            blocos += [Spacer(1, 3), tabela]
        return [Spacer(1, 3), _barra_secao(
            3, 'COMPONENTES E GRADES DO CONJUNTO', e, largura_util,
        ), *blocos]

    @staticmethod
    def _arte(
        item, e, largura_util=LARGURA_UTIL, altura_imagem=27 * mm,
        cor=AZUL, cor_clara=AZUL_CLARO, arredondada=False,
        *, altura_maxima=None, titulo=None,
    ) -> list:
        personalizacoes = list(item.personalizacoes.all())
        visuais = list(item.visuais.all())
        imagens_descritas = [
            (visual.imagem or (
                getattr(visual.mockup, 'imagem', None) if visual.mockup_id else None
            ), (visual.observacoes or '').strip())
            for visual in visuais
        ]
        imagens_descritas += [
            (personalizacao.arquivo, '')
            for personalizacao in personalizacoes
            if getattr(personalizacao, 'extensao', '') in DESENHAVEIS
        ]
        imagens_descritas = [(campo, texto) for campo, texto in imagens_descritas if campo]
        if not imagens_descritas:
            return []

        blocos = [
            _barra_secao(
                4, titulo or f'IMAGENS E IMPRESSÃO - {item.nome_exibicao}', e, largura_util,
                cor=cor, cor_clara=cor_clara, arredondada=arredondada,
            ),
            Spacer(1, 4),
        ]

        if altura_imagem == 27 * mm:
            tem_nomes = item.individuais.exists()
            if len(imagens_descritas) == 1:
                altura_imagem = (100 if tem_nomes else 132) * mm
            elif len(imagens_descritas) == 2:
                altura_imagem = (86 if tem_nomes else 120) * mm
            else:
                altura_imagem = 41 * mm
        por_linha = min(2, len(imagens_descritas))
        largura = largura_util / por_linha
        estilo_descricao = ParagraphStyle(
            'descricao_imagem', parent=e['pequeno'], alignment=1,
            fontName='Helvetica-Bold', textColor=colors.black,
        )
        descricoes = [
            Paragraph(esc(texto).replace('\n', '<br/>'), estilo_descricao) if texto else ''
            for _, texto in imagens_descritas
        ]
        alturas_descricoes = [
            max((descricao.wrap(largura - 6, 10000)[1] + 6
                 for descricao in descricoes[inicio:inicio + por_linha] if descricao), default=0)
            for inicio in range(0, len(descricoes), por_linha)
        ]
        if altura_maxima is not None:
            linhas = len(alturas_descricoes)
            # Na OP, usa todo o espaço medido acima do financeiro. Não
            # limita a imagem pela presença de nomes na outra coluna.
            limite = (altura_maxima - 9 * mm - 4 - sum(alturas_descricoes)) / linhas - 5 * mm
            altura_imagem = max(1, limite)
        for inicio in range(0, len(imagens_descritas), por_linha):
            faixa = imagens_descritas[inicio:inicio + por_linha]
            celulas = []
            for deslocamento, (campo, _) in enumerate(faixa):
                imagem = _imagem(campo, largura - 6 * mm, altura_imagem)
                conteudo = imagem or Paragraph('—', e['pequeno'])
                legenda = descricoes[inicio + deslocamento]
                if legenda:
                    conteudo = [conteudo, Spacer(1, 3), legenda]
                celulas.append(conteudo)
            while len(celulas) < por_linha:
                celulas.append('')
            grade = Table(
                [celulas], colWidths=[largura] * por_linha,
                rowHeights=[altura_imagem + 5 * mm + alturas_descricoes[inicio // por_linha]],
            )
            grade.setStyle(TableStyle([
                ('BOX', (0, 0), (-1, -1), .5, BORDA),
                ('INNERGRID', (0, 0), (-1, -1), .5, BORDA),
                ('BACKGROUND', (0, 0), (-1, -1), colors.white),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, 0), 'MIDDLE'),
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ]))
            # A linha inteira desce para a página seguinte se não houver
            # espaço suficiente.
            blocos.append(grade)

        return blocos

    @staticmethod
    def _grade(
        item, e, largura_util=LARGURA_UTIL, cor=AZUL,
        cor_clara=AZUL_CLARO, arredondada=False, titulo='GRADE',
    ) -> list:
        celulas = list(item.grade.all())
        if not celulas:
            return []

        # Os tamanhos vêm da grade lançada, não de uma lista fixa PP..G3: o
        # pedido pode ter tamanhos infantis ou plus size, e uma coluna fixa
        # esconderia justamente os que fogem do padrão.
        cabecalho = [c.tamanho.sigla for c in celulas] + ['TOTAL']
        valores = [str(c.quantidade) for c in celulas]
        total = sum(c.quantidade for c in celulas)
        valores.append(str(total))

        largura = min(largura_util / len(cabecalho), 22 * mm)
        tabela = _tabela(
            [cabecalho, valores], [largura] * len(cabecalho),
            cor_cabecalho=cor, fundo_linha=cor_clara, arredondada=arredondada,
        )
        tabela.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (-1, 1), (-1, 1), 'Helvetica-Bold'),
            ('BACKGROUND', (-1, 1), (-1, 1), FUNDO),
        ]))

        return [Spacer(1, 4), _barra_secao(
            3, titulo, e, largura_util, cor=cor, cor_clara=cor_clara,
            arredondada=arredondada,
        ), Spacer(1, 3), tabela]

    # ── Personalização individual ────────────────────────────────────────

    @staticmethod
    def _personalizacao(pedido, e, largura_util=LARGURA_UTIL) -> list:
        pessoas = list(pedido.individuais.all())
        if not pessoas:
            return []

        dados = [['#', 'Nome', 'Número', 'Tamanho', 'Produto']]
        for i, p in enumerate(pessoas, start=1):
            dados.append([
                str(i),
                Paragraph(esc(p.nome) or '—', e['celula']),
                p.numero or '—',
                p.tamanho.sigla if p.tamanho_id else '—',
                Paragraph(esc(p.item.nome_exibicao) if p.item_id else '—', e['celula']),
            ])

        larguras = [
            8 * mm, largura_util * 0.28, 15 * mm, 18 * mm,
            largura_util - (41 * mm + largura_util * 0.28),
        ]
        return [
            Spacer(1, 8),
            Paragraph(f'PERSONALIZAÇÃO POR PESSOA — {len(pessoas)}', e['secao']),
            _tabela(dados, larguras),
        ]

    @staticmethod
    def _personalizacao_item(
        item, e, largura_util=LARGURA_UTIL, cor=AZUL,
        cor_clara=AZUL_CLARO, arredondada=False,
        *, pessoas=None, inicio=0, colunas=1, fator_fonte=1,
    ) -> list:
        """Lista compacta e exclusiva do produto, sem repetir o nome dele."""
        pessoas = list(item.individuais.all()) if pessoas is None else pessoas
        if not pessoas:
            return []
        if item.eh_conjunto:
            dados = [['#', 'Nome camisa', 'Nº', 'Tam.', 'Nome calção', 'Nº', 'Tam.']]
            for indice, pessoa in enumerate(pessoas, start=inicio + 1):
                dados.append([
                    str(indice), Paragraph(esc(pessoa.nome) or '—', e['celula']),
                    pessoa.numero or '—', pessoa.tamanho.sigla,
                    Paragraph(esc(pessoa.nome_calcao) or '—', e['celula']),
                    pessoa.numero_calcao or '—',
                    pessoa.tamanho_calcao.sigla if pessoa.tamanho_calcao_id else '—',
                ])
            fixas = 8 * mm + 11 * mm + 13 * mm + 11 * mm + 13 * mm
            largura_nome = (largura_util - fixas) / 2
            tabela = _tabela(
                dados,
                [8 * mm, largura_nome, 11 * mm, 13 * mm,
                 largura_nome, 11 * mm, 13 * mm],
                cor_cabecalho=cor, fundo_linha=cor_clara,
                arredondada=arredondada,
            )
            return [
                Spacer(1, 5),
                _barra_secao(
                    None, f'PERSONALIZAÇÃO DO CONJUNTO - {len(pessoas)} ATLETA(S)',
                    e, largura_util, cor=cor, cor_clara=cor_clara,
                    arredondada=arredondada,
                ),
                Spacer(1, 3), tabela,
            ]
        grade = item.grade_tamanho.nome if item.grade_tamanho_id else 'Sem grade'
        titulo = f'PERSONALIZAÇÃO POR PESSOA - {len(pessoas)} | GRADE: {grade}'
        if colunas > 1:
            # Leitura vertical: 1, 2, 3... de cima para baixo; depois
            # continua no alto da coluna seguinte. Cada bloco repete seu
            # cabeçalho e mantém nome/número/tamanho juntos.
            base_pessoa = e.get('pessoa_compacta')
            fonte = base_pessoa.fontSize if base_pessoa else 6.3
            entrelinha = base_pessoa.leading if base_pessoa else 7.5
            estilo = ParagraphStyle(
                'pessoa_compacta', parent=e['celula'],
                fontSize=fonte * fator_fonte, leading=entrelinha * fator_fonte,
            )
            largura_bloco = (largura_util - (colunas - 1) * 2 * mm) / colunas
            larguras = []
            cabecalho = []
            for coluna in range(colunas):
                if coluna:
                    larguras.append(2 * mm)
                    cabecalho.append('')
                larguras += [5 * mm, largura_bloco - 23 * mm, 9 * mm, 9 * mm]
                cabecalho += ['#', 'Nome', 'Nº', 'Tam.']
            dados = [cabecalho]
            total_linhas = (len(pessoas) + colunas - 1) // colunas
            for linha in range(total_linhas):
                celulas = []
                for coluna in range(colunas):
                    if coluna:
                        celulas.append('')
                    posicao = linha + coluna * total_linhas
                    if posicao >= len(pessoas):
                        celulas += ['', '', '', '']
                        continue
                    pessoa = pessoas[posicao]
                    celulas += [
                        str(inicio + posicao + 1),
                        Paragraph(esc(pessoa.nome) or '—', estilo),
                        Paragraph(esc(pessoa.numero) or '—', estilo),
                        Paragraph(esc(pessoa.tamanho.sigla) if pessoa.tamanho_id else '—', estilo),
                    ]
                dados.append(celulas)
            tabela = _tabela(dados, larguras, cor_cabecalho=cor, fundo_linha=cor_clara)
            tabela.setStyle(TableStyle([
                ('FONTSIZE', (0, 0), (-1, -1), fonte * fator_fonte),
                ('LEADING', (0, 0), (-1, -1), entrelinha * fator_fonte),
                ('LEFTPADDING', (0, 0), (-1, -1), 2),
                ('RIGHTPADDING', (0, 0), (-1, -1), 2),
                ('TOPPADDING', (0, 0), (-1, -1), e.get('padding_tabela', 1.5) * fator_fonte),
                ('BOTTOMPADDING', (0, 0), (-1, -1), e.get('padding_tabela', 1.5) * fator_fonte),
            ] + [
                ('BACKGROUND', (coluna * 5 - 1, 0), (coluna * 5 - 1, -1), colors.white)
                for coluna in range(1, colunas)
            ]))
            return [
                Spacer(1, 5),
                _barra_secao(
                    None, titulo, e, largura_util,
                    cor=cor, cor_clara=cor_clara, arredondada=arredondada,
                ),
                Spacer(1, 3), tabela,
            ]
        dados = [['#', 'Nome', 'Número', 'Tamanho']]
        estilo = ParagraphStyle(
            'pessoa_coluna', parent=e['celula'],
            fontSize=e['celula'].fontSize * fator_fonte,
            leading=e['celula'].leading * fator_fonte,
        )
        for indice, pessoa in enumerate(pessoas, start=inicio + 1):
            dados.append([
                str(indice), Paragraph(esc(pessoa.nome) or '—', estilo),
                Paragraph(esc(pessoa.numero) or '—', estilo),
                Paragraph(esc(pessoa.tamanho.sigla) if pessoa.tamanho_id else '—', estilo),
            ])
        larguras = [8 * mm, largura_util - 43 * mm, 17 * mm, 18 * mm]
        tabela = _tabela(
            dados, larguras, cor_cabecalho=cor, fundo_linha=cor_clara,
            arredondada=arredondada,
        )
        tabela.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), estilo.fontSize),
            ('LEADING', (0, 0), (-1, -1), estilo.leading),
            ('TOPPADDING', (0, 0), (-1, -1), e.get('padding_tabela', 3) * fator_fonte),
            ('BOTTOMPADDING', (0, 0), (-1, -1), e.get('padding_tabela', 3) * fator_fonte),
        ]))
        return [
            Spacer(1, 5),
            _barra_secao(
                None, titulo, e, largura_util,
                cor=cor, cor_clara=cor_clara, arredondada=arredondada,
            ),
            Spacer(1, 3),
            tabela,
        ]

    # ── Observações operacionais ─────────────────────────────────────────

    @staticmethod
    def _quadro_observacoes(titulo, texto, e, largura_util, *, estilo_texto=None):
        """Título e conteúdo no mesmo quadro, alinhado às tabelas da coluna."""
        texto = (texto or '').strip()
        if not texto:
            return []
        texto = texto.replace('\r\n', '\n').replace('\r', '\n')
        estilo_titulo = ParagraphStyle(
            'titulo_observacoes', parent=e['secao'], fontSize=7.5, leading=9,
        )
        estilo_corpo = ParagraphStyle(
            'corpo_observacoes', parent=estilo_texto or e['normal'],
            textColor=colors.black, spaceBefore=0, spaceAfter=0,
        )
        quadro = Table([
            [Paragraph(esc(titulo), estilo_titulo)],
            [Paragraph(esc(texto).replace('\n', '<br/>'), estilo_corpo)],
        ], colWidths=[largura_util], hAlign='CENTER')
        quadro.setStyle(TableStyle([
            ('BOX', (0, 0), (-1, -1), .5, BORDA),
            ('BACKGROUND', (0, 0), (-1, 0), AZUL_CLARO),
            ('BACKGROUND', (0, 1), (-1, 1), FUNDO),
            ('LINEBELOW', (0, 0), (-1, 0), .35, BORDA),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), e.get('padding_observacao', 4)),
            ('BOTTOMPADDING', (0, 0), (-1, -1), e.get('padding_observacao', 4)),
        ]))
        return [Spacer(1, 5), quadro]

    @classmethod
    def _observacoes_op(cls, pedido, e, largura_util=LARGURA_UTIL) -> list:
        return cls._quadro_observacoes(
            'OBSERVAÇÕES DA OP', pedido.observacoes, e, largura_util,
        )

    # Mantido apenas como compatibilidade interna para chamadas antigas.
    # O gerador da OP não usa mais este bloco: valores e pagamentos não
    # pertencem à folha enviada para produção.

    @staticmethod
    def _financeiro(pedido, e, largura_util=LARGURA_UTIL, item=None) -> list:
        def brl(valor):
            return f'R$ {valor:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')

        linhas = [
            ('Conjuntos' if item and item.eh_conjunto else 'Peças', f'{item.quantidade if item else pedido.quantidade_total}'),
            ('Por conjunto' if item and item.eh_conjunto else 'Unitário', brl(item.valor_unitario or Decimal('0')) if item else brl(pedido.valor_unitario_medio)),
            ('Produto', brl(item.subtotal) if item else brl(pedido.subtotal)),
            ('TOTAL OP', brl(pedido.valor_total)),
        ]

        dados = [
            [Paragraph(
                f'<b>{rotulo}</b>', e['celula'],
            ) for rotulo, _ in linhas],
            [Paragraph(
                f'<b>{valor}</b>' if rotulo == 'TOTAL OP' else valor,
                ParagraphStyle(f'v-{indice}', parent=e['celula'], alignment=2),
            ) for indice, (rotulo, valor) in enumerate(linhas)],
        ]

        pagamento = []
        if pedido.forma_pagamento_id:
            pagamento.append(f'Forma: {pedido.forma_pagamento}')
        if pedido.condicao_pagamento_id:
            pagamento.append(f'Condição: {pedido.condicao_pagamento}')
        if pedido.entrada:
            pagamento += [f'Entrada: {brl(pedido.entrada)}', f'Saldo: {brl(pedido.saldo)}']

        blocos = [
            Spacer(1, 5),
            _barra_secao(None, 'FINANCEIRO', e, largura_util),
            Spacer(1, 3),
            _tabela(dados, [largura_util / len(linhas)] * len(linhas), cabecalho=False),
        ]
        if pagamento:
            blocos += [Spacer(1, 3), Paragraph(' · '.join(pagamento), e['normal'])]
        return blocos
