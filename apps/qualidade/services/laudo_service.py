"""
O laudo do lote — o boletim que acompanha a carga.

O QUE UM LAUDO É. É o documento que a fábrica assina dizendo "este lote foi
analisado, deu isto, e eu respondo por isso". Cliente exige, fiscalização pede,
e é ele que sustenta a conversa quando algo dá errado depois.

GERADO, E NÃO GUARDADO. O sistema monta o PDF na hora, a partir da análise.
Guardar o arquivo exigiria armazenamento e criaria a pergunta "qual versão
vale"; gerando, o laudo é sempre o que a análise diz agora. O campo
`laudo_pdf_url` continua existindo para o caso oposto — laudo de laboratório
externo, que veio pronto de fora e o sistema não tem como refazer.

TRÊS REGRAS QUE O DOCUMENTO IMPÕE, e que estão aqui e não na tela:

  · ANÁLISE PENDENTE NÃO VIRA LAUDO. Assinar que o lote foi analisado quando
    ninguém concluiu a análise é o oposto do que o documento serve para fazer.
  · O DESVIO APARECE NO LAUDO. Esconder o não conforme e mostrar só o resultado
    faria um documento mais bonito e sem valor nenhum — é justamente o desvio,
    com a ação tomada ao lado, que prova que a fábrica viu e tratou.
  · QUEM ASSINA VAI NO PAPEL. Laudo sem responsável técnico é uma folha com
    números; o nome é o que transforma medição em responsabilidade.
"""
from __future__ import annotations

from django.utils import timezone

from apps.core.services.exceptions import DomainError
from apps.qualidade.constants.enums import ResultadoAnalise


class LaudoService:
    """Monta o laudo de uma análise concluída."""

    # Resultados que autorizam emitir. Pendente fica de fora de propósito.
    EMITIVEIS = (
        ResultadoAnalise.APROVADO,
        ResultadoAnalise.APROVADO_COM_RESSALVA,
        ResultadoAnalise.REPROVADO,
    )

    @classmethod
    def pode_emitir(cls, analise) -> bool:
        return analise.resultado in cls.EMITIVEIS

    @classmethod
    def dados(cls, analise) -> dict:
        """
        Tudo que o laudo mostra, sem saber desenhar nada.

        SEPARADO DO PDF DE PROPÓSITO: assim a regra do documento — quem pode
        ser emitido, o que aparece, como o desvio é apresentado — tem teste que
        não depende de abrir um arquivo binário para conferir.
        """
        if not cls.pode_emitir(analise):
            raise DomainError(
                'Análise ainda pendente — conclua a análise antes de emitir o '
                'laudo. Um laudo de análise não concluída não certifica nada.'
            )

        itens = list(analise.itens.all().order_by('ordem', 'id'))
        nao_conformes = [
            i for i in itens
            if i.situacao == i.Situacao.NAO_CONFORME
        ]
        lote = analise.lote
        filial = analise.filial
        return {
            'analise': analise,
            'emitido_em': timezone.localtime(),
            'empresa': getattr(filial, 'empresa', None),
            'filial': filial,
            'lote': lote,
            'produto': getattr(lote, 'produto', None),
            'validade': getattr(lote, 'data_validade', None),
            'ordem': analise.ordem_producao,
            'responsavel': analise.responsavel_tecnico,
            'resultado': analise.get_resultado_display(),
            'reprovado': analise.resultado == ResultadoAnalise.REPROVADO,
            'com_ressalva': (
                analise.resultado == ResultadoAnalise.APROVADO_COM_RESSALVA
            ),
            'acao_reprovacao': (
                analise.get_acao_reprovacao_display()
                if analise.acao_reprovacao else ''
            ),
            'itens': itens,
            'nao_conformes': nao_conformes,
            # SEM TRATATIVA NO LAUDO é o que o cliente vai perguntar. Sai
            # marcado em vez de omitido: omitir seria o sistema ajudando a
            # esconder.
            'desvios_sem_acao': [
                i for i in nao_conformes if not i.acao_corretiva.strip()
            ],
            # `parametros` é o JSON livre, usado quando a análise não tem
            # checklist. Sem ele, laudo de análise antiga sairia em branco.
            'parametros_livres': (
                analise.parametros if not itens and analise.parametros else {}
            ),
        }

    @classmethod
    def numero(cls, analise) -> str:
        """
        A identificação do laudo, derivada e não guardada.

        Um contador próprio precisaria de tabela e daria buracos quando alguém
        gerasse e não usasse. Derivar da análise dá um número estável, único e
        que aponta de volta para o registro que o originou.
        """
        return f'LQ-{analise.data_analise:%Y}-{analise.pk:05d}'

    @classmethod
    def pdf(cls, analise) -> bytes:
        """O laudo em PDF, montado na hora."""
        from io import BytesIO

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )

        dados = cls.dados(analise)
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer, pagesize=A4,
            rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36,
            title=cls.numero(analise),
        )
        estilos = getSampleStyleSheet()
        corpo = estilos['Normal']
        elementos = []

        empresa = dados['empresa']
        if empresa is not None:
            elementos.append(Paragraph(str(empresa.razao_social), estilos['Heading2']))
            if empresa.cnpj:
                elementos.append(Paragraph(f'CNPJ {empresa.cnpj}', corpo))
        elementos += [
            Spacer(1, 10),
            Paragraph('Laudo de análise', estilos['Title']),
            Paragraph(
                f'{cls.numero(analise)} · emitido em '
                f'{dados["emitido_em"]:%d/%m/%Y %H:%M}',
                corpo,
            ),
            Spacer(1, 14),
        ]

        identificacao = [['Lote', dados['lote'].numero_lote if dados['lote'] else '—']]
        if dados['produto'] is not None:
            identificacao.append(['Produto', str(dados['produto'])])
        if dados['validade']:
            identificacao.append(['Validade', f'{dados["validade"]:%d/%m/%Y}'])
        if dados['ordem'] is not None:
            identificacao.append(['Ordem de produção', str(dados['ordem'].numero)])
        identificacao += [
            ['Tipo de análise', analise.get_tipo_analise_display()],
            ['Data da análise', f'{timezone.localtime(analise.data_analise):%d/%m/%Y %H:%M}'],
        ]
        tabela_id = Table(identificacao, colWidths=[130, 380])
        tabela_id.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elementos += [tabela_id, Spacer(1, 14)]

        if dados['itens']:
            linhas = [['Parâmetro', 'Medido', 'Exigido', 'Situação']]
            for item in dados['itens']:
                linhas.append([
                    item.nome_parametro,
                    item.valor or '—',
                    item.faixa or '—',
                    item.get_situacao_display(),
                ])
            tabela = Table(linhas, repeatRows=1, colWidths=[190, 100, 120, 100])
            estilo = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#d1d5db')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]
            # O DESVIO FICA VISÍVEL NA LINHA, e não só numa lista no fim: quem
            # confere o laudo lê a tabela, e uma cor ali é o que faz o olho
            # parar no que importa.
            for numero, item in enumerate(dados['itens'], start=1):
                if item.situacao == item.Situacao.NAO_CONFORME:
                    estilo.append(
                        ('TEXTCOLOR', (0, numero), (-1, numero),
                         colors.HexColor('#b91c1c'))
                    )
            tabela.setStyle(TableStyle(estilo))
            elementos += [tabela, Spacer(1, 14)]
        elif dados['parametros_livres']:
            elementos.append(Paragraph('Parâmetros medidos', estilos['Heading4']))
            for chave, valor in dados['parametros_livres'].items():
                elementos.append(Paragraph(f'{chave}: {valor}', corpo))
            elementos.append(Spacer(1, 14))

        elementos.append(Paragraph('Resultado', estilos['Heading4']))
        elementos.append(Paragraph(dados['resultado'], corpo))
        if dados['acao_reprovacao']:
            elementos.append(
                Paragraph(f'Destino do material: {dados["acao_reprovacao"]}', corpo)
            )
        if analise.observacao:
            elementos += [Spacer(1, 6), Paragraph(analise.observacao, corpo)]

        if dados['nao_conformes']:
            elementos += [
                Spacer(1, 12),
                Paragraph('Não conformidades e tratativas', estilos['Heading4']),
            ]
            for item in dados['nao_conformes']:
                acao = item.acao_corretiva.strip() or 'SEM TRATATIVA REGISTRADA'
                quem = item.acao_responsavel or '—'
                elementos.append(Paragraph(
                    f'<b>{item.nome_parametro}</b> — medido {item.valor or "—"}, '
                    f'exigido {item.faixa or "—"}.<br/>{acao} ({quem})',
                    corpo,
                ))
                elementos.append(Spacer(1, 4))

        elementos += [
            Spacer(1, 28),
            Paragraph('_' * 46, corpo),
            Paragraph(str(dados['responsavel']), corpo),
            Paragraph('Responsável técnico', corpo),
        ]

        doc.build(elementos)
        return buffer.getvalue()
