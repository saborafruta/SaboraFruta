"""
A travessia do recall, nos dois sentidos.

A tela de rastreabilidade existia e mostrava, como "componentes consumidos",
os itens da FICHA TÉCNICA. Está honestamente rotulada — serve para conferir a
formulação — mas num recall não responde nada: a receita diz "manga", e quem
precisa parar um lote precisa de "lote L-4471, do produtor Silva". São
perguntas diferentes, e a segunda é a única que faz o telefone tocar.

O dado sempre esteve lá: a baixa da OP grava o LOTE que o FEFO escolheu, e o
lote do acabado nasce com o id da OP. Os dois ponteiros fecham o circuito nos
dois sentidos — ninguém tinha ligado um no outro.

O que os testes cercam:

  · O CAMINHO PARA TRÁS chega ao lote de MP DE VERDADE, e daí ao produtor e à
    nota — não à linha da receita;
  · O CAMINHO PARA A FRENTE sai da fruta e chega ao cliente, passando pelas
    OPs e pelos acabados. Antes parava na primeira movimentação;
  · DESCE MAIS DE UM DEGRAU. Fruta → base → sorvete: parar no primeiro nível
    responderia "veio da base" e esconderia a fruta sob suspeita, que é a
    única coisa que importa;
  · LAÇO NÃO TRAVA A TELA. Cadastro errado que aponta para si mesmo tem de
    devolver o que já se sabe, não rodar para sempre.
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente, Fornecedor
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import LoteProduto, MovimentacaoEstoque
from apps.lotes.services.rastreio import RastreioService
from apps.producao.models import FichaTecnica, OrdemProducao
from apps.produtos.models import Produto, UnidadeMedida

HOJE = timezone.localdate()
DOC_OP = MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO
SAIDA = MovimentacaoEstoque.TipoOperacao.PRODUCAO_SAIDA


class RastreioBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Rastreio LTDA', nome_fantasia='Rastreio',
            cnpj='23345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='23345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='KG', descricao='Quilograma',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@rastreio.local', nome='Chefe', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )
        cls.produtor = Fornecedor.objects.create(
            filial=cls.filial, razao_social='Sítio do Silva',
            cpf_cnpj='11122233344',
        )
        cls.cliente_final = Cliente.objects.create(
            filial=cls.filial, razao_social='Mercado Bom Preço',
            cpf_cnpj='55566677788', ativo=True,
        )

    # ── Montagem da cadeia ───────────────────────────────────────────────

    def _produto(self, codigo, descricao):
        return Produto.objects.create(
            filial=self.filial, codigo=codigo, descricao=descricao,
            unidade_medida=self.unidade, controla_lote=True,
        )

    def _lote(self, produto, numero, quantidade='100', fornecedor=None, ordem=None):
        return LoteProduto.objects.create(
            filial=self.filial, produto=produto, numero_lote=numero,
            quantidade_inicial=Decimal(quantidade),
            quantidade_atual=Decimal(quantidade),
            custo_unitario=Decimal('10'),
            data_validade=HOJE + timedelta(days=180),
            fornecedor=fornecedor,
            ordem_producao_id=ordem.pk if ordem else None,
            status=LoteProduto.Status.ATIVO,
        )

    def _ordem(self, acabado, numero, quantidade='100'):
        ficha = FichaTecnica.objects.create(
            filial=self.filial, produto_acabado=acabado,
            quantidade_produzida=Decimal(quantidade),
        )
        return OrdemProducao.objects.create(
            filial=self.filial, numero=numero, produto_acabado=acabado,
            ficha_tecnica=ficha,
            quantidade_planejada=Decimal(quantidade),
            quantidade_produzida=Decimal(quantidade),
            status=OrdemProducao.Status.ENCERRADA,
            usuario_abertura=self.usuario,
        )

    def _consumiu(self, ordem, lote_mp, quantidade='120'):
        """O registro que a baixa por FEFO deixa — o elo que faltava ler."""
        return MovimentacaoEstoque.objects.create(
            filial=self.filial, produto=lote_mp.produto, lote=lote_mp,
            tipo_operacao=SAIDA, documento_tipo=DOC_OP,
            documento_id=ordem.pk, documento_numero=ordem.numero,
            quantidade=Decimal(quantidade),
            quantidade_anterior=Decimal('0'), quantidade_posterior=Decimal('0'),
            usuario=self.usuario, data_movimentacao=timezone.now(),
        )

    def _cadeia_simples(self):
        """Manga (lote da fruta) → OP → polpa (lote do acabado)."""
        self.manga = self._produto('MANGA', 'Manga in natura')
        self.polpa = self._produto('POLPA', 'Polpa de manga 100 g')
        self.lote_manga = self._lote(
            self.manga, 'FRUTA-001', '500', fornecedor=self.produtor,
        )
        self.op = self._ordem(self.polpa, 'OP-0001')
        self._consumiu(self.op, self.lote_manga, '120')
        self.lote_polpa = self._lote(self.polpa, 'POLPA-001', '100', ordem=self.op)
        return self.lote_polpa


class DeOndeVeioTests(RastreioBase):

    def test_o_acabado_chega_ao_lote_da_fruta(self):
        """
        O elo que a tela não tinha: do produto acabado ao LOTE de matéria-prima
        que de fato entrou nele — não à linha da receita.
        """
        acabado = self._cadeia_simples()

        caminho = RastreioService.de_onde_veio(acabado)

        lotes = [e.lote.numero_lote for e in caminho]
        self.assertEqual(lotes, ['POLPA-001', 'FRUTA-001'])

    def test_o_primeiro_elo_e_o_proprio_lote(self):
        """Sem ele a lista começa no meio da história."""
        acabado = self._cadeia_simples()

        caminho = RastreioService.de_onde_veio(acabado)

        self.assertEqual(caminho[0].lote, acabado)
        self.assertEqual(caminho[0].nivel, 0)

    def test_chega_ao_fornecedor(self):
        acabado = self._cadeia_simples()

        caminho = RastreioService.de_onde_veio(acabado)

        fruta = next(e for e in caminho if e.lote.numero_lote == 'FRUTA-001')
        self.assertEqual(fruta.fornecedor, self.produtor)

    def test_diz_quanto_de_cada_lote_entrou(self):
        """
        "Veio deste lote" não basta para um recall parcial: 120 kg de um lote
        de 500 diz quanto do lote está comprometido.
        """
        acabado = self._cadeia_simples()

        caminho = RastreioService.de_onde_veio(acabado)

        fruta = next(e for e in caminho if e.lote.numero_lote == 'FRUTA-001')
        self.assertEqual(fruta.quantidade, Decimal('120.000'))

    def test_soma_quando_a_op_comeu_o_mesmo_lote_em_duas_baixas(self):
        """
        FEFO pode gerar mais de um lançamento para o mesmo lote. Duas linhas
        do mesmo lote na tela fariam parecer duas origens diferentes.
        """
        acabado = self._cadeia_simples()
        self._consumiu(self.op, self.lote_manga, '30')

        caminho = RastreioService.de_onde_veio(acabado)

        fruta = [e for e in caminho if e.lote.numero_lote == 'FRUTA-001']
        self.assertEqual(len(fruta), 1)
        self.assertEqual(fruta[0].quantidade, Decimal('150.000'))

    def test_lote_comprado_sem_producao_para_nele_mesmo(self):
        manga = self._produto('MANGA', 'Manga in natura')
        lote = self._lote(manga, 'FRUTA-001', fornecedor=self.produtor)

        caminho = RastreioService.de_onde_veio(lote)

        self.assertEqual(len(caminho), 1)
        self.assertEqual(caminho[0].fornecedor, self.produtor)

    def test_desce_mais_de_um_degrau(self):
        """
        Fruta → base → sorvete. Parar no primeiro nível responderia "veio da
        base" e esconderia a fruta sob suspeita.
        """
        self._cadeia_simples()
        sorvete = self._produto('SORVETE', 'Sorvete de manga 2 L')
        op_sorvete = self._ordem(sorvete, 'OP-0002')
        self._consumiu(op_sorvete, self.lote_polpa, '40')
        lote_sorvete = self._lote(sorvete, 'SORV-001', ordem=op_sorvete)

        caminho = RastreioService.de_onde_veio(lote_sorvete)

        lotes = [e.lote.numero_lote for e in caminho]
        self.assertEqual(lotes, ['SORV-001', 'POLPA-001', 'FRUTA-001'])

    def test_o_nivel_marca_a_distancia(self):
        self._cadeia_simples()
        sorvete = self._produto('SORVETE', 'Sorvete de manga 2 L')
        op_sorvete = self._ordem(sorvete, 'OP-0002')
        self._consumiu(op_sorvete, self.lote_polpa, '40')
        lote_sorvete = self._lote(sorvete, 'SORV-001', ordem=op_sorvete)

        caminho = RastreioService.de_onde_veio(lote_sorvete)

        self.assertEqual([e.nivel for e in caminho], [0, 1, 2])


class ParaOndeFoiTests(RastreioBase):

    def test_a_fruta_chega_ao_acabado(self):
        """
        O caminho para a frente não existia: a tela mostrava movimentações
        cruas e parava ali, sem dizer o que a fruta virou.
        """
        self._cadeia_simples()

        caminho = RastreioService.para_onde_foi(self.lote_manga)

        lotes = [e.lote.numero_lote for e in caminho]
        self.assertEqual(lotes, ['FRUTA-001', 'POLPA-001'])

    def test_sobe_mais_de_um_degrau(self):
        self._cadeia_simples()
        sorvete = self._produto('SORVETE', 'Sorvete de manga 2 L')
        op_sorvete = self._ordem(sorvete, 'OP-0002')
        self._consumiu(op_sorvete, self.lote_polpa, '40')
        self._lote(sorvete, 'SORV-001', ordem=op_sorvete)

        caminho = RastreioService.para_onde_foi(self.lote_manga)

        lotes = [e.lote.numero_lote for e in caminho]
        self.assertEqual(lotes, ['FRUTA-001', 'POLPA-001', 'SORV-001'])

    def test_uma_fruta_em_duas_ops_aparece_nas_duas(self):
        """
        É o caso que o recall teme: o lote ruim entrou em mais de um produto.
        """
        self._cadeia_simples()
        outro = self._produto('SUCO', 'Suco de manga 1 L')
        op_suco = self._ordem(outro, 'OP-0003')
        self._consumiu(op_suco, self.lote_manga, '80')
        self._lote(outro, 'SUCO-001', ordem=op_suco)

        caminho = RastreioService.para_onde_foi(self.lote_manga)

        lotes = {e.lote.numero_lote for e in caminho}
        self.assertEqual(lotes, {'FRUTA-001', 'POLPA-001', 'SUCO-001'})

    def test_lote_que_nao_foi_a_lugar_nenhum(self):
        manga = self._produto('MANGA', 'Manga in natura')
        lote = self._lote(manga, 'FRUTA-001')

        caminho = RastreioService.para_onde_foi(lote)

        self.assertEqual(len(caminho), 1)


class LacoNaoTravaTests(RastreioBase):
    """Cadastro errado tem de devolver o que se sabe, não rodar para sempre."""

    def test_lote_que_aponta_para_a_propria_op_nao_roda_para_sempre(self):
        manga = self._produto('MANGA', 'Manga in natura')
        op = self._ordem(manga, 'OP-0001')
        lote = self._lote(manga, 'FRUTA-001', ordem=op)
        self._consumiu(op, lote, '10')  # a OP consome o lote que ela gerou

        caminho = RastreioService.de_onde_veio(lote)

        self.assertEqual(len(caminho), 1)

    def test_o_mesmo_laco_para_a_frente_tambem_para(self):
        manga = self._produto('MANGA', 'Manga in natura')
        op = self._ordem(manga, 'OP-0001')
        lote = self._lote(manga, 'FRUTA-001', ordem=op)
        self._consumiu(op, lote, '10')

        caminho = RastreioService.para_onde_foi(lote)

        self.assertEqual(len(caminho), 1)

    def test_a_profundidade_limita_a_descida(self):
        self._cadeia_simples()
        sorvete = self._produto('SORVETE', 'Sorvete de manga 2 L')
        op_sorvete = self._ordem(sorvete, 'OP-0002')
        self._consumiu(op_sorvete, self.lote_polpa, '40')
        lote_sorvete = self._lote(sorvete, 'SORV-001', ordem=op_sorvete)

        caminho = RastreioService.de_onde_veio(lote_sorvete, profundidade=1)

        self.assertEqual([e.lote.numero_lote for e in caminho],
                         ['SORV-001', 'POLPA-001'])


class ResumoDoRecallTests(RastreioBase):

    def test_o_resumo_junta_os_fornecedores_da_origem(self):
        """O que se lê primeiro quando o telefone toca."""
        acabado = self._cadeia_simples()
        origem = RastreioService.de_onde_veio(acabado)

        resumo = RastreioService.resumo(origem, [])

        self.assertIn(self.produtor, resumo['fornecedores'])

    def test_o_resumo_conta_os_lotes_do_caminho(self):
        acabado = self._cadeia_simples()
        origem = RastreioService.de_onde_veio(acabado)
        destino = RastreioService.para_onde_foi(self.lote_manga)

        resumo = RastreioService.resumo(origem, destino)

        self.assertEqual(resumo['lotes_origem'], 1)
        self.assertEqual(resumo['lotes_destino'], 1)

    def test_sem_caminho_o_resumo_vem_vazio_e_nao_estoura(self):
        manga = self._produto('MANGA', 'Manga in natura')
        lote = self._lote(manga, 'FRUTA-001')

        resumo = RastreioService.resumo(
            RastreioService.de_onde_veio(lote),
            RastreioService.para_onde_foi(lote),
        )

        self.assertEqual(resumo['fornecedores'], [])
        self.assertEqual(resumo['clientes'], [])


class TelaTests(RastreioBase):
    """A tela em si — é onde erro de template vira texto visível."""

    def setUp(self):
        self.client.force_login(self.usuario)

    def _abrir(self, lote):
        from django.urls import reverse
        return self.client.get(reverse('lotes:rastreabilidade'), {'lote': lote.pk})

    def test_a_tela_faz_as_duas_perguntas(self):
        acabado = self._cadeia_simples()

        resposta = self._abrir(acabado)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'De onde veio este produto?')
        self.assertContains(resposta, 'Onde foi parar?')

    def test_a_tela_mostra_o_lote_de_materia_prima(self):
        """
        O elo que faltava: o número do lote da fruta, na tela, e não a linha
        da receita.
        """
        acabado = self._cadeia_simples()

        resposta = self._abrir(acabado)

        self.assertContains(resposta, 'FRUTA-001')
        self.assertContains(resposta, 'Sítio do Silva')

    def test_a_tela_mostra_para_onde_a_fruta_foi(self):
        self._cadeia_simples()

        resposta = self._abrir(self.lote_manga)

        self.assertContains(resposta, 'POLPA-001')

    def test_nenhum_destino_explica_as_duas_leituras(self):
        """
        "Nenhum destino" pode ser lote parado na câmara ou saída que não grava
        lote. As duas pedem providências opostas, e a tela não pode deixar as
        duas com a mesma cara.
        """
        manga = self._produto('MANGA', 'Manga in natura')
        lote = self._lote(manga, 'FRUTA-001')

        resposta = self._abrir(lote)

        self.assertContains(resposta, 'não grava lote')

    def test_nada_de_tag_vaza_para_a_tela(self):
        acabado = self._cadeia_simples()

        corpo = self._abrir(acabado).content.decode()

        for marca in ('{%', '%}', '{#', '#}', 'endcomment'):
            self.assertNotIn(marca, corpo, f'tag {marca} vazou para a tela')
