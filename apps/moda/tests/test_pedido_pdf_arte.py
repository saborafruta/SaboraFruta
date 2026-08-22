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

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.moda.models import ArquivoPedido, PedidoProducao
from apps.moda.services.pedido_pdf import PedidoPdfService


def _png(cor=(220, 40, 30)) -> bytes:
    """Um PNG de verdade — o reportlab lê pelo PIL, não aceita bytes falsos."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGB', (120, 90), cor).save(buffer, 'PNG')
    return buffer.getvalue()


def _imagens(pdf: bytes) -> int:
    return len(re.findall(rb'/Subtype\s*/Image', pdf))


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

    # ── O que NÃO pode ir ────────────────────────────────────────────────

    def test_documento_interno_nao_entra_no_pdf(self):
        """
        O PDF vai para o cliente pelo link público. Documento é interno --
        contrato, planilha de custo -- e não pode sair junto.
        """
        pedido = self._pedido()
        antes = _imagens(PedidoPdfService.gerar(pedido))

        self._anexar(pedido, ArquivoPedido.Tipo.DOCUMENTO, nome='contrato.png')

        self.assertEqual(_imagens(PedidoPdfService.gerar(pedido)), antes)

    def test_outro_tambem_fica_de_fora(self):
        pedido = self._pedido()
        antes = _imagens(PedidoPdfService.gerar(pedido))

        self._anexar(pedido, ArquivoPedido.Tipo.OUTRO, nome='recado.png')

        self.assertEqual(_imagens(PedidoPdfService.gerar(pedido)), antes)

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
