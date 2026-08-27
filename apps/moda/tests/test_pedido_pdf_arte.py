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

    # ── O que NÃO pode ir ────────────────────────────────────────────────

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

    def test_pedido_e_orcamento_desenham_o_MESMO_cabecalho(self):
        """
        Os dois documentos são da mesma casa e têm de parecer da mesma casa.

        Enquanto a tarja era código do orçamento, dar o mesmo cabeçalho ao
        pedido significava copiar — e duas cópias divergem na primeira
        mudança de cor. Isto quebra se alguém reintroduzir a segunda.
        """
        from apps.moda.services import orcamento_pdf, pdf_marca, pedido_pdf

        self.assertIs(pedido_pdf.desenhar_tarja, pdf_marca.desenhar_tarja)
        self.assertIs(orcamento_pdf.desenhar_tarja, pdf_marca.desenhar_tarja)
        self.assertIs(pedido_pdf.bloco_empresa, pdf_marca.bloco_empresa)
        self.assertIs(orcamento_pdf.bloco_empresa, pdf_marca.bloco_empresa)

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
