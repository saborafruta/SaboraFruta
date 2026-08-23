"""
O comando que apaga pedidos — e o que ele NÃO pode fazer.

Um comando de limpeza é testado por medo, não por zelo: ele roda uma vez, em
produção, sem desfazer. Os três jeitos de ele estragar o dia:

  · apagar SEM confirmação. Quem digita o nome do comando para ver o que ele
    faz não pode perder o banco por isso;
  · apagar ALÉM do escopo. Uma limpeza que varre outras filiais é a que
    alguém roda achando que está no ambiente de teste;
  · apagar PELA METADE. A cadeia tem quatro `PROTECT`; na ordem errada ele
    estoura no primeiro pedido que virou OP e deixa o banco com ordem sem
    pedido e expedição sem ordem — pior do que não ter limpado.
"""
from datetime import date
from decimal import Decimal
from io import StringIO

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial
from apps.financeiro.models import ContaReceber
from apps.moda.models import (
    EtapaOrdem, Expedicao, Inspecao, ItemPedidoProducao, OrdemProducao,
    PedidoProducao, ProdutoModa, RegistroCorte,
)


class LimparPedidosBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Limpa LTDA', nome_fantasia='Limpa',
            cnpj='53345678000191',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='53345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.outra = Filial.objects.create(
            empresa=cls.empresa, razao_social='Filial 2', cnpj='53345678000353',
            uf='RN', cidade='Mossoro',
        )

    def setUp(self):
        self._n = 0

    def _pedido_completo(self, filial=None, com_ordem=True, com_corte=False,
                         baixado=False, com_expedicao=False, com_inspecao=False):
        """Um pedido com a cadeia inteira pendurada nele."""
        filial = filial or self.filial
        self._n += 1
        cliente = Cliente.objects.create(
            filial=filial, razao_social=f'Cliente {self._n}',
            cpf_cnpj=f'1234567890{self._n}',
        )
        produto = ProdutoModa.objects.create(
            filial=filial, codigo=f'CAM{self._n:03d}', nome='Camisa',
        )
        pedido = PedidoProducao.objects.create(
            filial=filial, cliente=cliente, numero=self._n,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=10,
        )
        if not com_ordem:
            return pedido

        ordem = OrdemProducao.objects.create(
            filial=filial, pedido=pedido, item=item,
            numero=f'OP-{self._n:04d}', ano=2026, sequencial=self._n,
            quantidade=10,
        )
        EtapaOrdem.objects.create(
            ordem=ordem, etapa=EtapaOrdem.Etapa.CORTE, sequencia=4,
            status=EtapaOrdem.Status.PENDENTE,
        )
        if com_corte:
            RegistroCorte.objects.create(
                filial=filial, ordem=ordem, numero=self._n,
                status=RegistroCorte.Status.CORTADO, quantidade=10,
                data=date(2026, 6, 1), consumo_real=Decimal('12'),
                estoque_baixado_em=timezone.now() if baixado else None,
            )
        if com_expedicao:
            Expedicao.objects.create(filial=filial, ordem=ordem, numero=self._n)
        if com_inspecao:
            Inspecao.objects.create(
                filial=filial, ordem=ordem, quantidade_inspecionada=10,
            )
        return pedido

    def _titulo(self, pedido):
        """Uma conta a receber gerada pelo pedido — vínculo solto."""
        from apps.moda.services.financeiro import FinanceiroPedidoService

        return ContaReceber.objects.create(
            filial=pedido.filial, cliente=pedido.cliente,
            documento_tipo=FinanceiroPedidoService.DOCUMENTO_TIPO,
            documento_id=pedido.pk, documento_numero='R1',
            data_emissao=date(2026, 6, 1), data_vencimento=date(2026, 6, 30),
            valor_original=Decimal('800'), valor_final=Decimal('800'),
            valor_saldo=Decimal('800'), status='aberto',
        )

    def _rodar(self, **opcoes):
        saida = StringIO()
        call_command('limpar_pedidos_moda', stdout=saida, stderr=saida, **opcoes)
        return saida.getvalue()


class SegurancaTests(LimparPedidosBase):
    """As travas — é por elas que o comando existe."""

    def test_sem_confirmar_nao_apaga_nada(self):
        """
        Quem digita o nome do comando para ver o que ele faz não pode perder
        o banco por isso.
        """
        self._pedido_completo(com_corte=True, com_expedicao=True)

        saida = self._rodar(filial=self.filial.pk)

        self.assertIn('ENSAIO', saida)
        self.assertEqual(PedidoProducao.objects.count(), 1)
        self.assertEqual(OrdemProducao.objects.count(), 1)

    def test_o_ensaio_conta_o_que_apagaria(self):
        self._pedido_completo(com_corte=True, com_expedicao=True, com_inspecao=True)

        saida = self._rodar(filial=self.filial.pk)

        for rotulo in ('Pedidos', 'Ordens de produção', 'Registros de corte',
                       'Expedições', 'Inspeções de qualidade'):
            self.assertIn(rotulo, saida)

    def test_filial_e_obrigatoria(self):
        """Limpeza sem escopo é a que alguém roda no ambiente errado."""
        with self.assertRaises(CommandError):
            call_command('limpar_pedidos_moda', stdout=StringIO())

    def test_filial_inexistente_para_o_comando(self):
        with self.assertRaises(CommandError):
            self._rodar(filial=99999)

    def test_nao_toca_em_outra_filial(self):
        alvo = self._pedido_completo(filial=self.filial, com_corte=True)
        vizinho = self._pedido_completo(filial=self.outra, com_corte=True)

        self._rodar(filial=self.filial.pk, confirmar=True)

        self.assertFalse(PedidoProducao.objects.filter(pk=alvo.pk).exists())
        self.assertTrue(PedidoProducao.objects.filter(pk=vizinho.pk).exists())
        self.assertEqual(
            OrdemProducao.objects.filter(pedido__filial=self.outra).count(), 1,
        )

    def test_filial_vazia_avisa_e_sai(self):
        saida = self._rodar(filial=self.filial.pk, confirmar=True)

        self.assertIn('Nada a apagar', saida)


class CadeiaTests(LimparPedidosBase):
    """A ordem de exclusão — quatro `PROTECT` na frente."""

    def test_apaga_a_cadeia_inteira(self):
        """
        Se a ordem estivesse errada, isto estouraria com `ProtectedError` em
        vez de falhar numa asserção — e é justamente esse o cenário.
        """
        self._pedido_completo(com_corte=True, com_expedicao=True, com_inspecao=True)

        self._rodar(filial=self.filial.pk, confirmar=True)

        self.assertEqual(PedidoProducao.objects.count(), 0)
        self.assertEqual(OrdemProducao.objects.count(), 0)
        self.assertEqual(RegistroCorte.objects.count(), 0)
        self.assertEqual(Expedicao.objects.count(), 0)
        self.assertEqual(Inspecao.objects.count(), 0)

    def test_pedido_sem_ordem_tambem_vai(self):
        """O orçamento que nunca virou OP é o caso mais simples e o mais comum."""
        self._pedido_completo(com_ordem=False)

        self._rodar(filial=self.filial.pk, confirmar=True)

        self.assertEqual(PedidoProducao.objects.count(), 0)

    def test_os_filhos_em_cascata_vao_junto(self):
        """Item e etapa são CASCADE: o banco os leva sem o comando pedir."""
        self._pedido_completo(com_ordem=True)

        self._rodar(filial=self.filial.pk, confirmar=True)

        self.assertEqual(ItemPedidoProducao.objects.count(), 0)
        self.assertEqual(EtapaOrdem.objects.count(), 0)


class FinanceiroTests(LimparPedidosBase):
    """O título é ligado por vínculo solto — o banco não o segue sozinho."""

    def test_sem_a_bandeira_o_titulo_fica_e_o_comando_avisa(self):
        pedido = self._pedido_completo(com_ordem=False)
        self._titulo(pedido)

        saida = self._rodar(filial=self.filial.pk, confirmar=True)

        self.assertIn('ficam órfãs', saida)
        self.assertEqual(ContaReceber.objects.count(), 1)

    def test_com_a_bandeira_o_titulo_vai_junto(self):
        pedido = self._pedido_completo(com_ordem=False)
        self._titulo(pedido)

        self._rodar(
            filial=self.filial.pk, confirmar=True, incluir_financeiro=True,
        )

        self.assertEqual(ContaReceber.objects.count(), 0)

    def test_nao_apaga_titulo_que_nao_veio_de_pedido(self):
        """
        Conta a receber lançada à mão no financeiro não tem nada a ver com
        esta limpeza — apagá-la seria destruir dado de outro módulo.
        """
        pedido = self._pedido_completo(com_ordem=False)
        self._titulo(pedido)
        avulso = ContaReceber.objects.create(
            filial=self.filial, cliente=pedido.cliente,
            documento_tipo='', documento_numero='AVULSO',
            data_emissao=date(2026, 6, 1), data_vencimento=date(2026, 6, 30),
            valor_original=Decimal('500'), valor_final=Decimal('500'),
            valor_saldo=Decimal('500'), status='aberto',
        )

        self._rodar(
            filial=self.filial.pk, confirmar=True, incluir_financeiro=True,
        )

        self.assertTrue(ContaReceber.objects.filter(pk=avulso.pk).exists())


class EstoqueTests(LimparPedidosBase):
    """O tecido baixado não volta — e a pessoa precisa saber antes."""

    def test_avisa_quando_ha_corte_com_estoque_baixado(self):
        self._pedido_completo(com_corte=True, baixado=True)

        saida = self._rodar(filial=self.filial.pk)

        self.assertIn('ATENÇÃO', saida)
        self.assertIn('NÃO devolve o tecido', saida)

    def test_nao_avisa_quando_nenhum_corte_baixou(self):
        self._pedido_completo(com_corte=True, baixado=False)

        saida = self._rodar(filial=self.filial.pk)

        self.assertNotIn('NÃO devolve o tecido', saida)


class DevolverEstoqueTests(LimparPedidosBase):
    """
    O caminho que mexe em OUTRO app — e por isso o mais arriscado daqui.

    Devolver saldo à mão, com um `UPDATE`, daria um número que só este
    comando entende. Ele chama os mesmos serviços das telas de estorno, e é
    isso que estes testes conferem: o saldo volta ao que era ANTES do corte,
    não a um valor que o comando inventou.
    """

    def setUp(self):
        super().setUp()
        from apps.produtos.models import Produto, UnidadeMedida

        self.metro = UnidadeMedida.objects.create(
            empresa=self.empresa, sigla='M', descricao='Metro',
            tipo=UnidadeMedida.Tipo.COMPRIMENTO,
        )
        self.produto_estoque = Produto.objects.create(
            filial=self.filial, codigo='TEC001', descricao='Malha Dry',
            unidade_medida=self.metro,
        )

    def _saldo(self, atual='500', reservado='0'):
        from apps.estoque.models.estoque import Estoque

        return Estoque.objects.create(
            produto=self.produto_estoque, filial=self.filial,
            quantidade_atual=Decimal(atual),
            quantidade_reservada=Decimal(reservado),
            quantidade_disponivel=Decimal(atual) - Decimal(reservado),
            custo_medio=Decimal('18'),
        )

    def _pedido_com_baixa(self, consumo='120'):
        """Um pedido cujo corte já tirou tecido do estoque, pelo serviço real."""
        from apps.moda.models import FichaTecnica, MaterialFicha
        from apps.moda.services.integracao import IntegracaoService

        self._n += 1
        cliente = Cliente.objects.create(
            filial=self.filial, razao_social=f'Cliente {self._n}',
            cpf_cnpj=f'9234567890{self._n}',
        )
        produto = ProdutoModa.objects.create(
            filial=self.filial, codigo=f'CAM{self._n:03d}', nome='Camisa',
        )
        ficha = FichaTecnica.objects.create(filial=self.filial, produto=produto)
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha Dry', unidade=MaterialFicha.Unidade.METRO,
            consumo=Decimal('1'), custo_unitario=Decimal('18'),
            produto_estoque=self.produto_estoque,
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=cliente, numero=self._n,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto, descricao='Camisa', quantidade=100,
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero=f'OP-{self._n:04d}', ano=2026, sequencial=self._n,
            quantidade=100,
        )
        corte = RegistroCorte.objects.create(
            filial=self.filial, ordem=ordem, numero=self._n,
            status=RegistroCorte.Status.CORTADO, quantidade=100,
            data=date(2026, 6, 1), consumo_real=Decimal(consumo),
        )
        # Pelo SERVIÇO, não à mão: é ele que grava a movimentação e mexe no
        # saldo, e o teste precisa do estado que a produção produz.
        IntegracaoService.baixar_estoque_do_corte(corte, usuario=self._usuario())
        return pedido, ordem, corte

    def _usuario(self):
        from apps.core.models import PerfilAcesso, Usuario

        if not hasattr(self, '_u'):
            perfil = PerfilAcesso.objects.create(
                empresa=self.empresa, nome=f'P{self._n}', is_admin=True,
            )
            self._u = Usuario.objects.create_user(
                email=f'u{self._n}@t.local', nome='Op', password='x' * 12,
                empresa=self.empresa, perfil=perfil, filial=self.filial,
            )
        return self._u

    def _saldo_atual(self):
        from apps.estoque.models.estoque import Estoque

        return Estoque.objects.get(
            produto=self.produto_estoque, filial=self.filial,
        )

    def test_devolve_o_tecido_que_o_corte_baixou(self):
        self._saldo(atual='500')
        self._pedido_com_baixa(consumo='120')

        self.assertEqual(self._saldo_atual().quantidade_atual, Decimal('380'))

        self._rodar(filial=self.filial.pk, confirmar=True,
                    incluir_estoque=True, usuario=self._usuario().email)

        self.assertEqual(self._saldo_atual().quantidade_atual, Decimal('500'))

    def test_sem_a_bandeira_o_saldo_fica_errado_de_proposito(self):
        """
        Não é descuido: desfazer estoque é decisão de quem opera, e o comando
        avisa em vez de decidir sozinho.
        """
        self._saldo(atual='500')
        self._pedido_com_baixa(consumo='120')

        self._rodar(filial=self.filial.pk, confirmar=True)

        self.assertEqual(self._saldo_atual().quantidade_atual, Decimal('380'))

    def test_apaga_as_movimentacoes_do_par(self):
        """
        Baixa e estorno existem para explicar um documento que está sendo
        apagado junto. Sozinhas, viram ruído no extrato do produto.
        """
        from apps.estoque.models.estoque import MovimentacaoEstoque

        self._saldo(atual='500')
        _, ordem, _ = self._pedido_com_baixa()

        self._rodar(filial=self.filial.pk, confirmar=True,
                    incluir_estoque=True, usuario=self._usuario().email)

        self.assertEqual(
            MovimentacaoEstoque.objects.filter(
                documento_tipo=MovimentacaoEstoque.DocumentoTipo.ORDEM_PRODUCAO,
                documento_id=ordem.pk,
            ).count(),
            0,
        )

    def test_o_aviso_muda_quando_vai_devolver(self):
        self._saldo(atual='500')
        self._pedido_com_baixa()

        sem = self._rodar(filial=self.filial.pk)
        com = self._rodar(filial=self.filial.pk, incluir_estoque=True,
                          usuario=self._usuario().email)

        self.assertIn('ATENÇÃO', sem)
        self.assertIn('NÃO devolve', sem)
        self.assertIn('deixa o saldo como estava', com)

    def test_tudo_liga_financeiro_e_estoque(self):
        self._saldo(atual='500')
        pedido, _, _ = self._pedido_com_baixa()
        self._titulo(pedido)

        self._rodar(filial=self.filial.pk, confirmar=True, tudo=True,
                    usuario=self._usuario().email)

        self.assertEqual(self._saldo_atual().quantidade_atual, Decimal('500'))
        self.assertEqual(ContaReceber.objects.count(), 0)
        self.assertEqual(PedidoProducao.objects.count(), 0)

    def test_exige_usuario_para_devolver_estoque(self):
        """
        A movimentação de estorno é atribuída a alguém. Carimbar o primeiro
        admin que aparecesse poria o nome de uma pessoa num lançamento que
        ela não fez — melhor exigir do que forjar.
        """
        self._saldo(atual='500')
        self._pedido_com_baixa()

        with self.assertRaises(CommandError):
            self._rodar(filial=self.filial.pk, confirmar=True, incluir_estoque=True)

    def test_usuario_inexistente_para_o_comando(self):
        self._saldo(atual='500')
        self._pedido_com_baixa()

        with self.assertRaises(CommandError):
            self._rodar(filial=self.filial.pk, confirmar=True,
                        incluir_estoque=True, usuario='ninguem@t.local')


class NaoSobraNadaTests(LimparPedidosBase):
    """
    Prova por VARREDURA, e não por leitura de chave estrangeira.

    Conferir cobertura a olho, FK por FK, é como se esquece uma: o modelo que
    ninguém lembrou fica na tabela apontando para um pedido que não existe
    mais, e só aparece meses depois num relatório que não fecha.

    Aqui o teste monta um pedido com TUDO que o comercial pendura nele, roda
    a limpeza e depois varre as tabelas do vertical procurando sobra. Modelo
    novo que entrar no fluxo e não for tratado quebra este teste — que é
    exatamente o que se quer dele.
    """

    # O que NÃO é gerado pelo comercial: cadastro de apoio, engenharia e
    # catálogo existem antes do pedido e continuam depois dele. Uma limpeza
    # que levasse o cadastro de tamanhos junto seria outro problema.
    CADASTROS = {
        'Tamanho', 'Cor', 'Tecido', 'Marca', 'Modelo', 'Categoria', 'Colecao',
        'Linha', 'Grade', 'ItemGrade', 'ProdutoModa', 'ProdutoCor', 'Variante',
        'Operacao', 'Roteiro', 'OperacaoRoteiro', 'FichaTecnica',
        'MaterialFicha', 'ImagemFicha', 'EstruturaProduto', 'Encaixe',
        'CapacidadeSetor', 'Posicao',
    }

    def _pedido_com_tudo(self):
        """Um pedido com cada coisa que o comercial sabe pendurar."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        from apps.moda.models import (
            AprovacaoPedido, ArquivoPedido, ItemGradePedido, Personalizacao,
            PersonalizacaoIndividual, Tamanho,
        )

        pedido = self._pedido_completo(
            com_corte=True, com_expedicao=True, com_inspecao=True,
        )
        item = pedido.itens.first()
        tamanho = Tamanho.objects.create(filial=self.filial, sigla='M', ordem=30)

        ItemGradePedido.objects.create(item=item, tamanho=tamanho, quantidade=10)
        Personalizacao.objects.create(
            item=item, tipo=Personalizacao.Tipo.ESCUDO, local='Peito',
            arquivo=SimpleUploadedFile('escudo.png', b'\x89PNG\r\n\x1a\n'),
        )
        PersonalizacaoIndividual.objects.create(
            pedido=pedido, item=item, tamanho=tamanho, nome='Joao', numero='10',
        )
        ArquivoPedido.objects.create(
            pedido=pedido, tipo=ArquivoPedido.Tipo.ARTE,
            arquivo=SimpleUploadedFile('arte.png', b'\x89PNG\r\n\x1a\n'),
        )
        AprovacaoPedido.objects.create(pedido=pedido)
        self._titulo(pedido)
        return pedido

    def test_nao_sobra_nada_do_pedido_em_tabela_nenhuma(self):
        from django.apps import apps as registro

        self._pedido_com_tudo()

        self._rodar(filial=self.filial.pk, confirmar=True, incluir_financeiro=True)

        sobrou = {}
        for modelo in registro.get_app_config('moda').get_models():
            if modelo.__name__ in self.CADASTROS:
                continue
            gerente = getattr(modelo, 'all_objects', modelo._default_manager)
            quantidade = gerente.count()
            if quantidade:
                sobrou[modelo.__name__] = quantidade

        self.assertEqual(
            sobrou, {},
            'sobrou registro do comercial depois da limpeza — '
            'algum modelo novo entrou no fluxo e não é tratado pelo comando',
        )

    def test_o_cadastro_de_apoio_continua_intacto(self):
        """
        Tamanho, cor e produto existem ANTES do pedido e continuam depois.
        Uma limpeza que levasse o catálogo junto seria outro problema — e
        bem pior, porque ele não se refaz sozinho.
        """
        from apps.moda.models import ProdutoModa, Tamanho

        self._pedido_com_tudo()

        self._rodar(filial=self.filial.pk, confirmar=True, incluir_financeiro=True)

        self.assertEqual(Tamanho.objects.count(), 1)
        self.assertEqual(ProdutoModa.objects.count(), 1)
