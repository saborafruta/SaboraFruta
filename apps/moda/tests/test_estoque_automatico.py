"""
As duas automações da ponte com o estoque.

RESERVAR AO COMEÇAR A PRODUZIR. Antes a reserva era manual, no painel de
necessidade: alguém tinha que lembrar. A OP entrava em produção e o material
seguia contado como disponível para todas as outras — duas ordens podiam
"ter" o mesmo rolo até uma delas chegar ao corte e descobrir que não tinha.

BAIXAR AO CORTAR. O botão era esquecido, e entre o corte e o clique o sistema
achava que havia 200 m de um rolo que já tinha virado peça. O painel de
necessidade lê esse saldo — um saldo alto demais esconde a falta de material
até o corte parar.

O que os testes cercam:

  · A RESERVA SAI SOZINHA na primeira etapa que entra em andamento — qualquer
    uma, não a primeira do roteiro. Roteiro se pula, e uma OP que começasse
    direto no corte produziria a peça inteira sem separar material;
  · UMA VEZ SÓ. Apontar a segunda etapa não reserva de novo, e reserva
    cancelada de propósito não é ressuscitada no apontamento seguinte;
  · NENHUMA DAS DUAS TRAVA O CHÃO DE FÁBRICA. Falha de estoque não pode
    impedir alguém de registrar que começou a cortar, nem de marcar o corte
    como cortado — o trabalho aconteceu de qualquer jeito, e o que se perde ao
    travar é o registro dele;
  · NÃO RESERVA O QUE NÃO EXISTE. Reserva maior que o saldo é promessa que o
    almoxarifado não cumpre, e sumiria da conta de disponível de todo mundo;
  · QUANDO A BAIXA NÃO SAI, A TELA DIZ POR QUÊ. Silêncio aqui é descobrir
    semanas depois que o estoque nunca desceu deste corte.
"""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.cadastros.models import Cliente
from apps.core.models import Empresa, Filial, PerfilAcesso, Usuario
from apps.estoque.models import Estoque, LoteProduto
from apps.moda.models import (
    EtapaOrdem, FichaTecnica, ItemPedidoProducao, MaterialFicha, OrdemProducao,
    PedidoProducao, ProdutoModa, RegistroCorte, ReservaMaterial,
)
from apps.moda.services.fluxo import SEQUENCIA, FluxoService
from apps.moda.services.necessidade import NecessidadeService
from apps.produtos.models import Produto, UnidadeMedida

HOJE = timezone.localdate()


class AutomacaoBase(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.empresa = Empresa.objects.create(
            razao_social='Confeccao Auto LTDA', nome_fantasia='Auto',
            cnpj='53345678000191', segmento='moda_confeccao',
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=cls.empresa, razao_social='Matriz', cnpj='53345678000272',
            uf='RN', cidade='Natal', is_matriz=True,
        )
        cls.cliente = Cliente.objects.create(
            filial=cls.filial, razao_social='Henry Freitas',
            cpf_cnpj='12345678901', ativo=True,
        )
        cls.unidade = UnidadeMedida.objects.create(
            empresa=cls.empresa, sigla='M', descricao='Metro',
        )
        perfil = PerfilAcesso.objects.create(
            empresa=cls.empresa, nome='Admin', is_admin=True,
        )
        cls.usuario = Usuario.objects.create_user(
            email='chefe@t.local', nome='Fulano', password='x' * 12,
            empresa=cls.empresa, perfil=perfil, filial=cls.filial,
        )

    def setUp(self):
        self.tecido = Produto.objects.create(
            filial=self.filial, codigo='TEC001', descricao='Malha PV',
            unidade_medida=self.unidade, controla_lote=True,
        )

    def _saldo(self, total, reservado='0'):
        disponivel = Decimal(total) - Decimal(reservado)
        Estoque.objects.update_or_create(
            produto=self.tecido, filial=self.filial,
            defaults={
                'quantidade_atual': Decimal(total),
                'quantidade_reservada': Decimal(reservado),
                'quantidade_disponivel': disponivel,
            },
        )
        LoteProduto.objects.update_or_create(
            filial=self.filial, produto=self.tecido, numero_lote='L1',
            defaults={
                'quantidade_inicial': Decimal(total),
                'quantidade_atual': Decimal(total),
                'custo_unitario': Decimal('10'),
                'data_validade': HOJE + timedelta(days=60),
            },
        )

    def _ordem(self, consumo='2', quantidade=10, numero=1):
        produto_moda = ProdutoModa.objects.create(
            filial=self.filial, codigo=f'CAM{numero:03d}', nome='Camisa',
        )
        ficha = FichaTecnica.objects.create(
            filial=self.filial, produto=produto_moda,
        )
        MaterialFicha.objects.create(
            ficha=ficha, tipo=MaterialFicha.Tipo.TECIDO_PRINCIPAL,
            descricao='Malha PV', consumo=Decimal(consumo),
            produto_estoque=self.tecido,
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=numero,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto_moda, descricao='Camisa',
            quantidade=quantidade, valor_unitario=Decimal('45'),
        )
        return OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero=f'OP-{numero:04d}', ano=2026, sequencial=numero,
            quantidade=quantidade,
        )

    def _etapa(self, ordem, etapa='corte'):
        sequencia = dict(SEQUENCIA).get(etapa, 10)
        return EtapaOrdem.objects.create(
            ordem=ordem, etapa=etapa, sequencia=sequencia,
        )

    def _iniciar(self, etapa):
        return FluxoService.apontar(
            etapa, self.usuario, {'status': EtapaOrdem.Status.EM_ANDAMENTO},
        )

    def _reservas(self, ordem):
        return ReservaMaterial.all_objects.filter(
            ordem=ordem, status=ReservaMaterial.Status.ATIVA,
        )


class ReservaAoIniciarTests(AutomacaoBase):

    def test_apontar_a_primeira_etapa_separa_o_material(self):
        self._saldo(100)
        ordem = self._ordem(consumo='2', quantidade=10)

        self._iniciar(self._etapa(ordem))

        reserva = self._reservas(ordem).get()
        self.assertEqual(reserva.quantidade, Decimal('20.0000'))
        self.assertEqual(reserva.produto_id, self.tecido.pk)

    def test_qualquer_etapa_serve_de_gatilho(self):
        """
        Roteiro se pula. Se a reserva dependesse de uma etapa específica, uma
        OP que começasse direto no corte produziria a peça inteira sem nunca
        separar material.
        """
        self._saldo(100)
        ordem = self._ordem()

        self._iniciar(self._etapa(ordem, etapa='costura'))

        self.assertEqual(self._reservas(ordem).count(), 1)

    def test_o_estoque_passa_a_contar_o_material_como_reservado(self):
        """
        É o efeito que importa: sem isto, duas ordens 'têm' o mesmo rolo até
        uma delas chegar ao corte.
        """
        self._saldo(100)
        ordem = self._ordem(consumo='2', quantidade=10)

        self._iniciar(self._etapa(ordem))

        estoque = Estoque.objects.get(produto=self.tecido, filial=self.filial)
        self.assertEqual(estoque.quantidade_reservada, Decimal('20.000'))
        self.assertEqual(estoque.quantidade_disponivel, Decimal('80.000'))

    def test_a_ordem_fica_carimbada(self):
        self._saldo(100)
        ordem = self._ordem()

        self._iniciar(self._etapa(ordem))

        ordem.refresh_from_db()
        self.assertIsNotNone(ordem.material_reservado_em)

    def test_a_segunda_etapa_nao_reserva_de_novo(self):
        self._saldo(100)
        ordem = self._ordem(consumo='2', quantidade=10)
        self._iniciar(self._etapa(ordem, etapa='corte'))

        self._iniciar(self._etapa(ordem, etapa='costura'))

        self.assertEqual(self._reservas(ordem).count(), 1)

    def test_reserva_cancelada_nao_ressuscita(self):
        """
        Alguém cancelou a reserva de propósito — liberar material para uma OP
        mais urgente é decisão legítima. O apontamento seguinte não pode
        desfazê-la pelas costas.
        """
        self._saldo(100)
        ordem = self._ordem()
        self._iniciar(self._etapa(ordem, etapa='corte'))
        reserva = self._reservas(ordem).get()
        NecessidadeService.cancelar_reserva(reserva, self.usuario)

        self._iniciar(self._etapa(ordem, etapa='costura'))

        self.assertEqual(self._reservas(ordem).count(), 0)

    def test_so_o_status_em_andamento_dispara(self):
        self._saldo(100)
        ordem = self._ordem()
        etapa = self._etapa(ordem)

        FluxoService.apontar(etapa, self.usuario, {'responsavel': 'Maria'})

        self.assertEqual(self._reservas(ordem).count(), 0)
        ordem.refresh_from_db()
        self.assertIsNone(ordem.material_reservado_em)


class ReservaNaoInventaMaterialTests(AutomacaoBase):

    def test_sem_saldo_nao_reserva(self):
        """
        Reserva maior que o saldo é promessa que o almoxarifado não cumpre —
        e sumiria da conta de disponível de todas as outras ordens.
        """
        self._saldo(0)
        ordem = self._ordem(consumo='2', quantidade=10)

        self._iniciar(self._etapa(ordem))

        self.assertEqual(self._reservas(ordem).count(), 0)

    def test_com_saldo_parcial_reserva_o_que_da(self):
        """
        Não é tudo ou nada: o que está separado fica separado, e o déficit
        continua aparecendo no painel de necessidade.
        """
        self._saldo(12)
        ordem = self._ordem(consumo='2', quantidade=10)  # precisa de 20

        self._iniciar(self._etapa(ordem))

        self.assertEqual(self._reservas(ordem).get().quantidade, Decimal('12.0000'))

    def test_material_sem_produto_de_estoque_e_ignorado(self):
        self._saldo(100)
        ordem = self._ordem()
        MaterialFicha.objects.create(
            ficha=ordem.ficha, tipo=MaterialFicha.Tipo.AVIAMENTO,
            descricao='Zíper sem cadastro', consumo=Decimal('1'),
        )

        self._iniciar(self._etapa(ordem))

        self.assertEqual(self._reservas(ordem).count(), 1)

    def test_ordem_sem_ficha_nao_estoura(self):
        self._saldo(100)
        produto_moda = ProdutoModa.objects.create(
            filial=self.filial, codigo='SEMFICHA', nome='Sem ficha',
        )
        pedido = PedidoProducao.objects.create(
            filial=self.filial, cliente=self.cliente, numero=77,
        )
        item = ItemPedidoProducao.objects.create(
            pedido=pedido, produto=produto_moda, descricao='Sem ficha',
            quantidade=5, valor_unitario=Decimal('10'),
        )
        ordem = OrdemProducao.objects.create(
            filial=self.filial, pedido=pedido, item=item,
            numero='OP-0077', ano=2026, sequencial=77, quantidade=5,
        )

        self._iniciar(self._etapa(ordem))

        self.assertEqual(self._reservas(ordem).count(), 0)


class NaoTravaOChaoDeFabricaTests(AutomacaoBase):

    def test_falha_na_reserva_nao_impede_o_apontamento(self):
        """
        Quem está no terminal marcando que começou a cortar não pode receber
        um erro de estoque na cara e ficar sem registrar o apontamento.
        """
        self._saldo(100)
        ordem = self._ordem()
        etapa = self._etapa(ordem)

        with patch.object(
            NecessidadeService, 'reservar_da_ordem',
            side_effect=RuntimeError('banco fora'),
        ):
            alterados = self._iniciar(etapa)

        self.assertIn('status', alterados)
        etapa.refresh_from_db()
        self.assertEqual(etapa.status, EtapaOrdem.Status.EM_ANDAMENTO)

    def test_falha_deixa_sem_carimbo_para_tentar_de_novo(self):
        """
        Erro transitório de banco não pode marcar a ordem como já reservada —
        senão o material nunca é separado.
        """
        self._saldo(100)
        ordem = self._ordem()

        with patch.object(
            NecessidadeService, 'reservar_da_ordem',
            side_effect=RuntimeError('banco fora'),
        ):
            self._iniciar(self._etapa(ordem, etapa='corte'))

        ordem.refresh_from_db()
        self.assertIsNone(ordem.material_reservado_em)

        self._iniciar(self._etapa(ordem, etapa='costura'))

        self.assertEqual(self._reservas(ordem).count(), 1)


class BaixaAoCortarTests(AutomacaoBase):

    def setUp(self):
        super().setUp()
        self.client.force_login(self.usuario)

    def _salvar_corte(self, ordem, status, consumo='20'):
        return self.client.post(
            reverse('moda:corte-create'),
            {
                'ordem': ordem.pk,
                'consumo_real': consumo,
                'status': status,
                'data': HOJE.strftime('%Y-%m-%d'),
                # O formulário do enfesto cobra a conta do encaixe inteira.
                'largura_tecido': '1.60',
                'comprimento_encaixe': '5.00',
                'folhas': 4,
                'aproveitamento': '85',
                'consumo_planejado': '20',
            },
            follow=True,
        )

    def test_marcar_como_cortado_ja_baixa_o_estoque(self):
        self._saldo(100)
        ordem = self._ordem()

        self._salvar_corte(ordem, RegistroCorte.Status.CORTADO)

        corte = RegistroCorte.all_objects.get(ordem=ordem)
        self.assertIsNotNone(corte.estoque_baixado_em)
        estoque = Estoque.objects.get(produto=self.tecido, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('80.000'))

    def test_a_baixa_automatica_respeita_fefo(self):
        """A automação não pode ter uma segunda regra de seleção."""
        self._saldo(100)
        ordem = self._ordem()

        self._salvar_corte(ordem, RegistroCorte.Status.CORTADO)

        corte = RegistroCorte.all_objects.get(ordem=ordem)
        consumo = corte.consumos_lote.get()
        self.assertEqual(consumo.lote.numero_lote, 'L1')
        self.assertEqual(consumo.quantidade, Decimal('20.0000'))

    def test_corte_planejado_nao_baixa(self):
        self._saldo(100)
        ordem = self._ordem()

        self._salvar_corte(ordem, RegistroCorte.Status.PLANEJADO)

        corte = RegistroCorte.all_objects.get(ordem=ordem)
        self.assertIsNone(corte.estoque_baixado_em)

    def test_a_tela_confirma_a_baixa(self):
        self._saldo(100)
        ordem = self._ordem()

        resposta = self._salvar_corte(ordem, RegistroCorte.Status.CORTADO)

        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(
            any('Baixa automática' in m for m in mensagens),
            f'esperava confirmação da baixa, veio: {mensagens}',
        )

    def test_sem_consumo_real_o_corte_salva_e_avisa(self):
        """
        O consumo real chega zerado quando o corte é marcado antes de alguém
        medir. Travar o "cortado" por causa disso puniria o chão de fábrica
        por um número que ainda não existe — mas o silêncio seria pior.
        """
        self._saldo(100)
        ordem = self._ordem()

        resposta = self._salvar_corte(
            ordem, RegistroCorte.Status.CORTADO, consumo='0',
        )

        corte = RegistroCorte.all_objects.get(ordem=ordem)
        self.assertEqual(corte.status, RegistroCorte.Status.CORTADO)
        self.assertIsNone(corte.estoque_baixado_em)
        mensagens = [str(m) for m in resposta.context['messages']]
        self.assertTrue(
            any('Estoque não baixado' in m for m in mensagens),
            f'esperava aviso do motivo, veio: {mensagens}',
        )

    def test_o_botao_continua_funcionando_como_segunda_porta(self):
        """
        Ele existe para o que o automático não alcança — e para estornar.
        """
        from apps.moda.services.integracao import IntegracaoService

        self._saldo(100)
        ordem = self._ordem()
        self._salvar_corte(ordem, RegistroCorte.Status.CORTADO, consumo='0')
        corte = RegistroCorte.all_objects.get(ordem=ordem)
        corte.consumo_real = Decimal('20')
        corte.save(update_fields=['consumo_real'])

        baixa = IntegracaoService.baixar_estoque_do_corte(corte, self.usuario)

        self.assertEqual(baixa.quantidade, Decimal('20.0000'))

    def test_baixa_automatica_nao_repete_no_segundo_save(self):
        from apps.moda.services.integracao import IntegracaoService

        self._saldo(100)
        ordem = self._ordem()
        self._salvar_corte(ordem, RegistroCorte.Status.CORTADO)
        corte = RegistroCorte.all_objects.get(ordem=ordem)

        self.assertIsNone(IntegracaoService.baixar_ao_cortar(corte, self.usuario))

        estoque = Estoque.objects.get(produto=self.tecido, filial=self.filial)
        self.assertEqual(estoque.quantidade_atual, Decimal('80.000'))
