"""
A arte no PDF do pedido — e o que NÃO pode ir junto.

O mesmo arquivo é servido pelo escritório e pelo link público do cliente.
Por isso o teste que mais importa aqui não é o de "a imagem apareceu", e sim
o de que documento interno continua de fora: um vazamento desses manda
contrato e planilha de custo para o WhatsApp do cliente, e ninguém percebe
porque o PDF abre normalmente.
"""
import io
import re
from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import (
    ArquivoPedido, ItemPedidoProducao, PedidoProducao, VisualItemPedido,
    PersonalizacaoIndividual, Tamanho,
)
from apps.moda.services.orcamento_pdf import OrcamentoPdfService
from apps.moda.services.pedido_pdf import (
    PedidoPdfService, _destino_qr, mensagem_whatsapp,
)


def _png(cor=(220, 40, 30)) -> bytes:
    """Um PNG de verdade — o reportlab lê pelo PIL, não aceita bytes falsos."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (120, 90), cor).save(buffer, 'PNG')
    return buffer.getvalue()


def _imagens(pdf: bytes) -> int:
    return len(re.findall(rb'/Subtype\s*/Image', pdf))


def _paginas(pdf: bytes) -> int:
    """`/Type /Pages` (o catálogo) não conta — só as páginas em si."""
    return len(re.findall(rb'/Type\s*/Page[^s]', pdf))


class ArteNoPdfTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Arte LTDA', nome_fantasia='Arte',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Confeccao Arte LTDA',
            cnpj='53345678000272', uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Diego Macedo', cpf_cnpj='12345678901',
        )

    def _pedido(self):
        return PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=12,
        )

    @staticmethod
    def _anexar(pedido, tipo, nome='arte.png', descricao='', cor=(220, 40, 30)):
        return ArquivoPedido.objects.create(
            pedido=pedido, tipo=tipo, descricao=descricao,
            arquivo=SimpleUploadedFile(nome, _png(cor), content_type='image/png'),
        )

    # ── A arte entra ─────────────────────────────────────────────────────

    def test_pedido_sem_arte_serve_de_linha_de_base(self):
        """Sem arte o PDF ainda sai — com marca e QR, e nada mais."""
        pdf = PedidoPdfService.gerar(self._pedido())
        self.base = _imagens(pdf)
        self.assertTrue(pdf.startswith(b'%PDF'))

    def test_arte_do_pedido_vira_imagem_no_pdf(self):
        pedido = self._pedido()
        antes = _imagens(PedidoPdfService.gerar(pedido))

        self._anexar(pedido, ArquivoPedido.Tipo.ARTE, descricao='Frente camisa')

        self.assertEqual(_imagens(PedidoPdfService.gerar(pedido)), antes + 1)

    def test_referencia_do_cliente_tambem_entra(self):
        pedido = self._pedido()
        antes = _imagens(PedidoPdfService.gerar(pedido))

        self._anexar(pedido, ArquivoPedido.Tipo.REFERENCIA, nome='ref.png')

        self.assertEqual(_imagens(PedidoPdfService.gerar(pedido)), antes + 1)

    def test_varias_artes_entram_todas(self):
        """
        Quatro artes, quatro imagens — e elas precisam ser DIFERENTES entre
        si no teste. O reportlab reaproveita imagem repetida num objeto só,
        então quatro arquivos byte a byte iguais contariam como um e o teste
        acusaria um defeito que não existe.
        """
        pedido = self._pedido()
        antes = _imagens(PedidoPdfService.gerar(pedido))

        for i, cor in enumerate([(200, 0, 0), (0, 200, 0), (0, 0, 200), (200, 200, 0)]):
            self._anexar(pedido, ArquivoPedido.Tipo.ARTE, nome=f'arte{i}.png', cor=cor)

        self.assertEqual(_imagens(PedidoPdfService.gerar(pedido)), antes + 4)

    def test_varios_mockups_da_mesma_posicao_entram_no_pdf(self):
        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa', quantidade=5,
        )
        antes = _imagens(PedidoPdfService.gerar(pedido))
        visuais = []
        for indice, cor in enumerate([
            (200, 0, 0), (0, 200, 0), (0, 0, 200), (200, 200, 0), (120, 30, 180),
        ]):
            visuais.append(VisualItemPedido.objects.create(
                item=item,
                posicao='frente_camisa',
                imagem=SimpleUploadedFile(
                    f'frente-{indice}.png', _png(cor), content_type='image/png',
                ),
            ))

        self.assertEqual(_imagens(PedidoPdfService.gerar(pedido)), antes + 5)
        for visual in visuais:
            visual.imagem.delete(save=False)

    def test_pdf_nao_imprime_nome_da_tecnica_abaixo_das_imagens(self):
        from apps.moda.models import Personalizacao
        from apps.moda.services.pedido_pdf import LARGURA_UTIL, _estilos

        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa', quantidade=1,
        )
        Personalizacao.objects.create(
            item=item, tecnica=Personalizacao.Tecnica.DTF,
        )
        visual = VisualItemPedido.objects.create(
            item=item, posicao='frente_camisa',
            imagem=SimpleUploadedFile('sem-legenda.png', _png()),
        )
        self.addCleanup(visual.imagem.delete, save=False)

        texto = self._texto_layout(
            PedidoPdfService._arte(item, _estilos(), LARGURA_UTIL / 2),
        )

        self.assertNotIn('DTF', texto)

    def test_imagem_unica_cresce_quando_nao_ha_lista_de_nomes(self):
        from reportlab.lib.units import mm

        from apps.moda.services.pedido_pdf import LARGURA_UTIL, _estilos

        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa', quantidade=1,
        )
        visual = VisualItemPedido.objects.create(
            item=item, posicao='frente_camisa',
            imagem=SimpleUploadedFile('imagem-grande.png', _png()),
        )
        self.addCleanup(visual.imagem.delete, save=False)

        altura_sem_nomes = PedidoPdfService._arte(
            item, _estilos(), LARGURA_UTIL / 2,
        )[2]._argH[0]
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='P')
        PersonalizacaoIndividual.objects.create(
            pedido=pedido, item=item, tamanho=tamanho, nome='Pessoa 1',
        )
        altura_com_nomes = PedidoPdfService._arte(
            item, _estilos(), LARGURA_UTIL / 2,
        )[2]._argH[0]

        self.assertEqual(altura_sem_nomes, 137 * mm)
        self.assertEqual(altura_com_nomes, 105 * mm)
        self.assertGreater(altura_sem_nomes, altura_com_nomes)

    def test_orcamento_tambem_exibe_todos_os_anexos(self):
        pedido = self._pedido()
        antes = _imagens(OrcamentoPdfService.gerar(pedido))
        self._anexar(
            pedido, ArquivoPedido.Tipo.DOCUMENTO,
            nome='documento.png', cor=(15, 80, 180),
        )

        self.assertEqual(_imagens(OrcamentoPdfService.gerar(pedido)), antes + 1)

    def test_orcamento_exibe_pagamento_previsto_validade_e_prazo_padrao(self):
        from reportlab.platypus import Paragraph, Table

        from apps.moda.services.orcamento_pdf import _estilos

        pedido = self._pedido()
        pedido.previsao_pagamento = [
            {'forma': 'pix', 'valor': '40.00'},
            {'forma': 'credito_parcelado', 'valor': '60.00'},
        ]
        pedido.save(update_fields=['previsao_pagamento'])
        estilos = _estilos()

        pagamento = OrcamentoPdfService._pagamento_previsto(pedido, estilos)
        tabela = [bloco for bloco in pagamento if isinstance(bloco, Table)][-1]
        texto_pagamento = ' '.join(
            celula.getPlainText() if isinstance(celula, Paragraph) else str(celula)
            for linha in tabela._cellvalues for celula in linha
        )
        quadro_observacoes = [
            bloco for bloco in OrcamentoPdfService._observacoes(pedido, estilos)
            if isinstance(bloco, Table)
        ][-1]
        observacoes = ' '.join(
            celula.getPlainText() if isinstance(celula, Paragraph) else str(celula)
            for linha in quadro_observacoes._cellvalues for celula in linha
        )

        self.assertIn('PIX', texto_pagamento)
        self.assertIn('Crédito parcelado', texto_pagamento)
        self.assertIn('válido por 5 dias', observacoes)
        self.assertIn('até 30 dias úteis', observacoes)
        self.assertIn(
            '* O pagamento de 50% do valor total deverá ser realizado na aprovação '
            'do pedido, para início da produção. Os 50% restantes deverão ser pagos '
            'no ato da entrega.',
            observacoes,
        )

    # ── O que NÃO pode ir ────────────────────────────────────────────────

    @staticmethod
    def _texto_layout(bloco):
        from reportlab.platypus import Paragraph, Table

        if isinstance(bloco, Paragraph):
            return bloco.getPlainText()
        if isinstance(bloco, Table):
            return ArteNoPdfTests._texto_layout(bloco._cellvalues)
        if isinstance(bloco, (list, tuple)):
            return ' '.join(ArteNoPdfTests._texto_layout(b) for b in bloco)
        return ''

    def test_orcamento_reune_foto_estrutura_grade_e_nomes_por_produto(self):
        from apps.moda.models import ItemGradePedido, PersonalizacaoIndividual, Tamanho
        from apps.moda.services.orcamento_pdf import _estilos

        pedido = self._pedido()
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M')
        for indice, nome in enumerate(('Camisa Adulto', 'Camisa Oversized')):
            item = ItemPedidoProducao.objects.create(
                pedido=pedido, descricao=nome, quantidade=1,
                valor_unitario=Decimal('70'),
                observacoes=f'Estrutura da peça:\nMalha: TECIDO-{indice}\nAbertura: ZIPER 10 CM',
            )
            ItemGradePedido.objects.create(item=item, tamanho=tamanho, quantidade=1)
            PersonalizacaoIndividual.objects.create(
                pedido=pedido, item=item, tamanho=tamanho, nome=f'PESSOA-{indice}',
            )
            visual = VisualItemPedido.objects.create(
                item=item, posicao='frente_camisa',
                imagem=SimpleUploadedFile(f'agrupamento-{indice}.png', _png()),
            )
            self.addCleanup(visual.imagem.delete, save=False)

        tabela = OrcamentoPdfService._itens(pedido, _estilos())[0]
        self.assertEqual(tabela.repeatRows, 1)
        self.assertEqual(len(tabela._cellvalues), 3)
        for indice, linha in enumerate(tabela._cellvalues[1:]):
            texto = self._texto_layout(linha)
            self.assertIn(f'TECIDO-{indice}', texto)
            self.assertIn(f'PESSOA-{indice}', texto)
            self.assertNotIn(f'PESSOA-{1 - indice}', texto)
            self.assertIn('Grade:', texto)
            self.assertIn('R$70,00', texto)
            self.assertTrue(linha[0])
        self.assertEqual(_paginas(OrcamentoPdfService.gerar(pedido)), 1)

    def test_orcamento_nao_trunca_estrutura_e_escapa_textos(self):
        from apps.moda.services.orcamento_pdf import _estilos

        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa <A> & B',
            observacoes='Estrutura da peça:\n' + '\n'.join(
                f'Opção {i}: VALOR-{i} & detalhe' for i in range(25)
            ),
        )
        texto = self._texto_layout(OrcamentoPdfService._produto(item, _estilos()))
        self.assertIn('VALOR-24 & detalhe', texto)
        self.assertIn('Camisa <A> & B', texto)
        self.assertTrue(OrcamentoPdfService.gerar(pedido).startswith(b'%PDF'))

    def test_orcamento_organiza_especificacoes_em_duas_colunas_sem_reduzir_imagem(self):
        from reportlab.platypus import Table
        from apps.moda.services.orcamento_pdf import _estilos

        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Agasalho',
            observacoes='Estrutura da peça:\n' + '\n'.join(
                f'Tipo {indice}: VALOR-{indice}' for indice in range(10)
            ),
        )

        linha = OrcamentoPdfService._produto(item, _estilos())
        conteudo = linha[1]
        tabela = next(bloco for bloco in conteudo if isinstance(bloco, Table))
        self.assertEqual(len(tabela._cellvalues), 5)
        self.assertEqual(len(tabela._cellvalues[0]), 2)
        self.assertAlmostEqual(OrcamentoPdfService.LARGURAS[0], 27 * 72 / 25.4)
        self.assertAlmostEqual(OrcamentoPdfService.LARGURAS[1], 76 * 72 / 25.4)

    def test_resumo_comercial_e_um_bloco_unico_para_nao_quebrar_total(self):
        from reportlab.platypus import KeepTogether
        from apps.moda.services.orcamento_pdf import _estilos

        pedido = self._pedido()
        pedido.previsao_pagamento = [
            {'forma': 'boleto', 'valor': '500.00'},
            {'forma': 'dinheiro', 'valor': '500.00'},
        ]
        pedido.save(update_fields=['previsao_pagamento'])

        blocos = OrcamentoPdfService._resumo_comercial(pedido, _estilos())
        self.assertIsInstance(blocos[0], KeepTogether)
        texto_financeiro = self._texto_layout(blocos[0]._content)
        texto_fechamento = self._texto_layout(blocos[2]._content)
        self.assertIn('Total:', texto_financeiro)
        self.assertIn('FORMA DE PAGAMENTO PREVISTA', texto_financeiro)
        self.assertIn('Observações e prazos:', texto_fechamento)
        self.assertIn('Previsão de entrega:', texto_fechamento)

    def test_orcamento_usa_previsao_de_entrega_e_mantem_prazo_maximo(self):
        from datetime import date
        from apps.moda.services.orcamento_pdf import _estilos

        pedido = self._pedido()
        pedido.data_prevista_entrega = date(2026, 8, 31)
        texto = self._texto_layout(OrcamentoPdfService._previsao_entrega(pedido, _estilos()))
        self.assertIn('Previsão de entrega:', texto)
        self.assertIn('31/08/2026', texto)
        self.assertNotIn('Prazo de entrega:', texto)
        observacoes = self._texto_layout(OrcamentoPdfService._observacoes(pedido, _estilos()))
        self.assertIn('válido por 5 dias', observacoes)
        self.assertIn('prazo máximo de entrega é de até 30 dias úteis', observacoes)

    def test_orcamento_sem_data_e_pagamento_nao_inventa_informacoes(self):
        from apps.moda.services.orcamento_pdf import _estilos

        pedido = self._pedido()
        self.assertIn('Não informada', self._texto_layout(
            OrcamentoPdfService._pagamento_previsto(pedido, _estilos()),
        ))
        self.assertIn('A combinar', self._texto_layout(
            OrcamentoPdfService._previsao_entrega(pedido, _estilos()),
        ))

    def test_orcamento_lista_grande_de_pessoas_pagina_sem_perder_nomes(self):
        from apps.moda.models import ItemGradePedido, PersonalizacaoIndividual, Tamanho
        from apps.moda.services.orcamento_pdf import _estilos

        pedido = self._pedido()
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M')
        item = ItemPedidoProducao.objects.create(pedido=pedido, descricao='Time completo', quantidade=150)
        ItemGradePedido.objects.create(item=item, tamanho=tamanho, quantidade=150)
        PersonalizacaoIndividual.objects.bulk_create([
            PersonalizacaoIndividual(pedido=pedido, item=item, tamanho=tamanho, nome=f'ATLETA-{i:03d}')
            for i in range(150)
        ])
        texto = self._texto_layout(OrcamentoPdfService._produto(item, _estilos()))
        for i in range(150):
            self.assertIn(f'ATLETA-{i:03d}', texto)
        self.assertGreater(_paginas(OrcamentoPdfService.gerar(pedido)), 1)

    def test_orcamento_mostra_todas_as_imagens_do_produto(self):
        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(pedido=pedido, descricao='Camisa')
        antes = _imagens(OrcamentoPdfService.gerar(pedido))
        for indice in range(5):
            visual = VisualItemPedido.objects.create(
                item=item, posicao='frente_camisa',
                imagem=SimpleUploadedFile(f'orc-foto-{indice}.png', _png((indice * 30, 50, 100))),
            )
            self.addCleanup(visual.imagem.delete, save=False)
        self.assertEqual(_imagens(OrcamentoPdfService.gerar(pedido)), antes + 5)

    def test_documento_anexado_tambem_entra_no_pdf(self):
        pedido = self._pedido()
        antes = _imagens(PedidoPdfService.gerar(pedido))

        self._anexar(pedido, ArquivoPedido.Tipo.DOCUMENTO, nome='contrato.png')

        self.assertEqual(_imagens(PedidoPdfService.gerar(pedido)), antes + 1)

    def test_outro_anexo_tambem_entra_no_pdf(self):
        pedido = self._pedido()
        antes = _imagens(PedidoPdfService.gerar(pedido))

        self._anexar(pedido, ArquivoPedido.Tipo.OUTRO, nome='recado.png')

        self.assertEqual(_imagens(PedidoPdfService.gerar(pedido)), antes + 1)

    def test_arquivo_que_nao_e_imagem_nao_quebra_o_pdf(self):
        """
        CDR e PDF não viram desenho. O documento tem de sair assim mesmo,
        com o NOME do arquivo no lugar -- some a imagem, não a informação.
        """
        pedido = self._pedido()
        ArquivoPedido.objects.create(
            pedido=pedido, tipo=ArquivoPedido.Tipo.ARTE,
            arquivo=SimpleUploadedFile('escudo.cdr', b'nao-e-imagem'),
        )

        pdf = PedidoPdfService.gerar(pedido)

        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1000)

    # ── Escape ───────────────────────────────────────────────────────────

    def test_e_comercial_na_razao_social_nao_corrompe_o_documento(self):
        """
        "L&R SPORTS LTDA" saía impresso "L&R; SPORTS LTDA".

        O `Paragraph` do reportlab interpreta um dialeto de XML e tentava ler
        `&R` como entidade. A razão social da casa tem `&`, então isto vinha
        errado em todo pedido que já saiu.
        """
        from apps.moda.services.pedido_pdf import esc

        self.assertEqual(esc('L&R SPORTS LTDA'), 'L&amp;R SPORTS LTDA')
        self.assertEqual(esc('Tam < 5 > 3'), 'Tam &lt; 5 &gt; 3')
        self.assertEqual(esc(None), '')

    def test_pdf_sai_inteiro_com_caractere_de_xml_no_cadastro(self):
        pedido = self._pedido()
        pedido.observacoes = 'Entregar até <sexta> & confirmar'
        pedido.save(update_fields=['observacoes'])

        pdf = PedidoPdfService.gerar(pedido)

        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertGreater(len(pdf), 1000)

    # ── Produtos um abaixo do outro ──────────────────────────────────────

    def test_cada_produto_ocupa_uma_folha_propria(self):
        """
        A ficha de produção é destacável: cada produto precisa trazer todas
        as próprias informações em uma folha exclusiva.
        """
        from apps.moda.models import ItemPedidoProducao

        pedido = self._pedido()
        for nome in ['Camisa Adulto', 'Camisa Baby Look', 'Calcao']:
            ItemPedidoProducao.objects.create(
                pedido=pedido, descricao=nome, quantidade=5,
            )

        self.assertEqual(_paginas(PedidoPdfService.gerar(pedido)), 3)

    def test_um_produto_so_continua_numa_folha(self):
        from apps.moda.models import ItemPedidoProducao

        pedido = self._pedido()
        ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa Adulto', quantidade=5,
        )

        pdf = PedidoPdfService.gerar(pedido)
        self.assertEqual(_paginas(pdf), 1)
        self.assertRegex(
            pdf,
            rb'/MediaBox\s*\[\s*0\s+0\s+841\.\d+\s+595\.\d+\s*\]',
        )

    def test_personalizacoes_em_colunas_preservam_ordem_e_todos_os_campos(self):
        from apps.moda.services.pedido_pdf import _estilos, LARGURA_UTIL

        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(pedido=pedido, descricao='Camisa', quantidade=22)
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='XGG')
        pessoas = [PersonalizacaoIndividual(
            pedido=pedido, item=item, tamanho=tamanho, nome=f'Pessoa {n}', numero=str(n),
        ) for n in range(1, 23)]
        tabela = PedidoPdfService._personalizacao_item(
            item, _estilos(), LARGURA_UTIL / 2, pessoas=pessoas, colunas=2,
        )[-1]
        self.assertEqual(len(tabela._cellvalues), 12)  # 11 linhas + cabeçalho
        self.assertEqual(tabela._cellvalues[1][1].getPlainText(), 'Pessoa 1')
        self.assertEqual(tabela._cellvalues[2][1].getPlainText(), 'Pessoa 2')
        self.assertEqual(tabela._cellvalues[1][6].getPlainText(), 'Pessoa 12')
        self.assertEqual(tabela._cellvalues[-1][6].getPlainText(), 'Pessoa 22')
        self.assertEqual(tabela._cellvalues[-1][7].getPlainText(), '22')
        self.assertEqual(tabela._cellvalues[-1][8].getPlainText(), 'XGG')

    def test_tres_colunas_sao_preenchidas_de_cima_para_baixo(self):
        from apps.moda.services.pedido_pdf import LARGURA_UTIL, _estilos

        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa', quantidade=22,
        )
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='P')
        pessoas = [PersonalizacaoIndividual(
            pedido=pedido, item=item, tamanho=tamanho,
            nome=f'Pessoa {numero}', numero=str(numero),
        ) for numero in range(1, 23)]

        tabela = PedidoPdfService._personalizacao_item(
            item, _estilos(), LARGURA_UTIL / 2,
            pessoas=pessoas, colunas=3,
        )[-1]

        self.assertEqual(
            [linha[0] for linha in tabela._cellvalues[1:]],
            [str(numero) for numero in range(1, 9)],
        )
        self.assertEqual(
            [linha[5] for linha in tabela._cellvalues[1:]],
            [str(numero) for numero in range(9, 17)],
        )
        self.assertEqual(
            [linha[10] for linha in tabela._cellvalues[1:]],
            [str(numero) for numero in range(17, 23)] + ['', ''],
        )

    def test_muitos_nomes_continuam_em_outra_pagina_sem_reduzir_arte(self):
        from reportlab.platypus import KeepInFrame

        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(pedido=pedido, descricao='Camisa', quantidade=180)
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='XGG')
        visual = VisualItemPedido.objects.create(
            item=item, posicao='frente_camisa',
            imagem=SimpleUploadedFile('teste-colunas.png', _png(), content_type='image/png'),
        )
        self.addCleanup(visual.imagem.delete, save=False)
        frames = []

        def registrar_frame(*args, **kwargs):
            frame = KeepInFrame(*args, **kwargs)
            frames.append(frame)
            return frame

        with patch('apps.moda.services.pedido_pdf.KeepInFrame', side_effect=registrar_frame):
            PedidoPdfService.gerar(pedido)
        escala_sem_nomes = getattr(frames[0], '_scale', 1)
        PersonalizacaoIndividual.objects.bulk_create([
            PersonalizacaoIndividual(
                pedido=pedido, item=item, tamanho=tamanho,
                nome=f'Nome completo de teste da pessoa {n}', numero=str(n),
            ) for n in range(180)
        ])
        frames.clear()
        with patch('apps.moda.services.pedido_pdf.KeepInFrame', side_effect=registrar_frame):
            pdf = PedidoPdfService.gerar(pedido)
        self.assertGreater(_paginas(pdf), 1)
        self.assertEqual(getattr(frames[0], '_scale', 1), escala_sem_nomes)
        self.assertEqual(frames[2].mode, 'error')

    def test_coluna_direita_pode_ocupar_toda_altura_util_da_pagina(self):
        """O contêiner externo não pode descontar padding da altura já medida."""
        from reportlab.lib.units import mm
        from reportlab.platypus import Spacer, Table

        from apps.moda.services.pedido_pdf import PAGINA

        pedido = self._pedido()
        ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa com personalização', quantidade=1,
        )
        altura_util = PAGINA[1] - 31 * mm - 9 * mm - 14
        arte = [Spacer(1, altura_util - 15)]
        personalizacao = [Table([['Nome']], rowHeights=[10])]

        with (
            patch.object(PedidoPdfService, '_arte', return_value=arte),
            patch.object(
                PedidoPdfService, '_personalizacao_item',
                return_value=personalizacao,
            ),
        ):
            pdf = PedidoPdfService.gerar(pedido)

        self.assertTrue(pdf.startswith(b'%PDF'))
        self.assertEqual(_paginas(pdf), 1)

    def test_financeiro_e_observacoes_sao_montados_em_todas_as_paginas(self):
        pedido = self._pedido()
        pedido.observacoes = 'Conferir nomes antes da entrega.'
        pedido.save(update_fields=['observacoes'])
        itens = [
            ItemPedidoProducao.objects.create(
                pedido=pedido, descricao='Camisa', quantidade=3,
                valor_unitario=Decimal('20.00'),
            ),
            ItemPedidoProducao.objects.create(
                pedido=pedido, descricao='Calção', quantidade=2,
                valor_unitario=Decimal('15.00'),
            ),
        ]

        financeiro_original = PedidoPdfService._financeiro
        with patch.object(
            PedidoPdfService, '_financeiro', wraps=financeiro_original,
        ) as financeiro:
            PedidoPdfService.gerar(pedido)

        self.assertEqual(financeiro.call_count, 2)
        self.assertEqual(
            [chamada.kwargs['item'].pk for chamada in financeiro.call_args_list],
            [item.pk for item in itens],
        )

    def test_financeiro_da_op_nao_exibe_desconto_acrescimo_e_frete(self):
        from apps.moda.services.pedido_pdf import LARGURA_UTIL, _estilos

        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa', quantidade=3,
            valor_unitario=Decimal('20.00'),
        )

        tabela = PedidoPdfService._financeiro(
            pedido, _estilos(), LARGURA_UTIL / 2, item=item,
        )[3]
        cabecalhos = [celula.getPlainText() for celula in tabela._cellvalues[0]]

        self.assertEqual(cabecalhos, ['Peças', 'Unitário', 'Produto', 'TOTAL OP'])

    def test_observacao_do_produto_tem_rotulo_e_texto_em_negrito(self):
        from reportlab.lib import colors
        from reportlab.platypus import Paragraph

        from apps.moda.services.pedido_pdf import LARGURA_UTIL, _estilos

        pedido = self._pedido()
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, descricao='Camisa', quantidade=1,
            observacoes='Conferir gola antes de cortar.',
        )

        paragrafos = [
            bloco for bloco in PedidoPdfService._item(
                pedido, item, _estilos(), 0, largura_util=LARGURA_UTIL / 2,
            ) if isinstance(bloco, Paragraph)
        ]
        observacao = next(
            bloco for bloco in paragrafos
            if bloco.getPlainText().startswith('Obs do produto:')
        )

        self.assertEqual(
            observacao.getPlainText(),
            'Obs do produto: Conferir gola antes de cortar.',
        )
        self.assertEqual(observacao.style.fontName, 'Helvetica-Bold')
        self.assertEqual(observacao.style.textColor, colors.black)
        self.assertGreater(observacao.style.fontSize, _estilos()['pequeno'].fontSize)

    def test_pdf_nao_reserva_rodape_e_qr_fica_no_cabecalho(self):
        pedido = self._pedido()

        with patch.object(
            PedidoPdfService, '_cabecalho_folha',
            wraps=PedidoPdfService._cabecalho_folha,
        ) as cabecalho:
            pdf = PedidoPdfService.gerar(pedido, 'https://ited.app.br/')

        self.assertTrue(pdf.startswith(b'%PDF'))
        cabecalho.assert_called_once_with(pedido, 'https://ited.app.br/')
        self.assertFalse(hasattr(PedidoPdfService, '_rodape'))

    def test_qr_do_pdf_aponta_para_conferencia_de_entrega(self):
        pedido = self._pedido()

        self.assertEqual(
            _destino_qr(pedido, 'https://ited.app.br/'),
            'https://ited.app.br' + reverse(
                'moda:pedido-conferencia', args=[pedido.pk],
            ),
        )

    def test_destino_do_qr_nao_interrompe_mensagem_do_whatsapp(self):
        pedido = self._pedido()

        mensagem = mensagem_whatsapp(pedido, 'https://ited.app.br/pedido/exemplo/')

        self.assertIn('Diego Macedo', mensagem)
        self.assertIn('https://ited.app.br/pedido/exemplo/', mensagem)

    # ── Cabeçalho ────────────────────────────────────────────────────────

    def test_novo_layout_do_orcamento_nao_altera_cabecalho_da_producao(self):
        """O orçamento tem desenho próprio, mas usa a marca cadastrada."""
        from apps.moda.services import orcamento_pdf, pdf_marca, pedido_pdf

        self.assertIs(pedido_pdf.desenhar_tarja, pdf_marca.desenhar_tarja)
        self.assertIs(pedido_pdf.bloco_empresa, pdf_marca.bloco_empresa)
        self.assertIs(orcamento_pdf.logo, pdf_marca.logo)
        with patch.object(orcamento_pdf, 'logo', return_value=None) as marca:
            OrcamentoPdfService.gerar(self._pedido())
        self.assertEqual(marca.call_args.args[0], self.filial)

    def test_endereco_nao_sai_com_parenteses_vazio(self):
        """
        `str(filial)` devolve "Eureka (/)" quando cidade e UF estão em
        branco, e era isso que ia impresso no cabeçalho. O bloco monta campo
        a campo e omite o que falta.
        """
        from apps.moda.services.pdf_marca import bloco_empresa, estilos_empresa

        self.filial.cidade = ''
        self.filial.uf = ''
        self.filial.save(update_fields=['cidade', 'uf'])

        texto = self._texto_do_bloco(bloco_empresa(self.filial, estilos_empresa()))

        self.assertNotIn('(/)', texto)
        self.assertIn('Confeccao Arte LTDA', texto)

    def test_endereco_e_contato_caem_para_a_empresa_quando_a_filial_nao_tem(self):
        """
        Filial recém-criada vem só com nome e CNPJ, e o endereço está
        cadastrado na EMPRESA. Sem a queda, o documento saía com o nome e
        mais nada — enquanto o dado estava ali do lado.
        """
        from apps.moda.services.pdf_marca import bloco_empresa, estilos_empresa

        self.empresa.endereco = 'Rua Meira Brandao'
        self.empresa.numero = '206'
        self.empresa.bairro = 'Barro Vermelho'
        self.empresa.cidade = 'Natal'
        self.empresa.uf = 'RN'
        self.empresa.cep = '59000000'
        self.empresa.email = 'contato@erk.com'
        self.empresa.telefone = '(84) 98792-9443'
        self.empresa.site = 'www.useerk.com'
        self.empresa.save()

        texto = self._texto_do_bloco(bloco_empresa(self.filial, estilos_empresa()))

        self.assertIn('Rua Meira Brandao, 206, Barro Vermelho', texto)
        self.assertIn('Natal - RN | CEP: 59000-000', texto)
        self.assertIn('contato@erk.com', texto)
        self.assertIn('www.useerk.com', texto)

    def test_endereco_da_filial_vence_o_da_empresa(self):
        """Filial com endereço próprio é OUTRO endereço, não um detalhe."""
        from apps.moda.services.pdf_marca import bloco_empresa, estilos_empresa

        self.empresa.cidade = 'Natal'
        self.empresa.save(update_fields=['cidade'])
        self.filial.cidade = 'Joao Pessoa'
        self.filial.save(update_fields=['cidade'])

        texto = self._texto_do_bloco(bloco_empresa(self.filial, estilos_empresa()))

        self.assertIn('Joao Pessoa', texto)
        self.assertNotIn('Natal', texto)

    @staticmethod
    def _texto_do_bloco(bloco) -> str:
        return ' '.join(
            celula.text for linha in bloco._cellvalues for celula in linha
            if hasattr(celula, 'text')
        )

    def test_a_regra_de_visibilidade_e_uma_so(self):
        """
        A página do link e o PDF leem a MESMA lista. Se alguém acrescentar um
        tipo novo em um lugar e esquecer o outro, isto quebra antes de virar
        vazamento em produção.
        """
        from apps.moda.models.arquivo import TIPOS_VISIVEIS_AO_CLIENTE
        from apps.moda.views_publico import (
            TIPOS_VISIVEIS_AO_CLIENTE as DA_PAGINA,
        )

        self.assertIs(TIPOS_VISIVEIS_AO_CLIENTE, DA_PAGINA)
        self.assertNotIn(ArquivoPedido.Tipo.DOCUMENTO, TIPOS_VISIVEIS_AO_CLIENTE)
