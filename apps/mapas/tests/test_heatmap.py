"""
Mapa de calor de vendas (secao 10).

O que importa aqui e o que o mapa acende: as duas origens de venda somadas,
Permuta/Doacao fora da receita, os filtros, e o peso normalizado que o
leaflet.heat espera.
"""
import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class BaseHeatmap(TestCase):
    _seq = 0

    def setUp(self):
        self.filial = self._empresa('Alfa', '11222333000181')
        self.usuario = self._usuario(self.filial)
        self._logar(self.usuario, self.filial)

    def _empresa(self, nome, cnpj):
        from apps.core.models import Empresa, Filial

        emp = Empresa.objects.create(
            razao_social=nome, cnpj=cnpj,
            regime_tributario='simples', codigo_regime_tributario=1,
        )
        return Filial.objects.create(
            empresa=emp, razao_social=nome, nome_fantasia=nome,
            cnpj=cnpj, uf='RN', is_matriz=True,
        )

    def _filial_extra(self, empresa, nome, cnpj):
        from apps.core.models import Filial

        return Filial.objects.create(
            empresa=empresa, razao_social=nome, nome_fantasia=nome,
            cnpj=cnpj, uf='RN', is_matriz=False,
        )

    def _usuario(self, filial, *, admin=True):
        from apps.core.models import PerfilAcesso, Usuario

        BaseHeatmap._seq += 1
        n = BaseHeatmap._seq
        perfil = PerfilAcesso.objects.create(
            empresa=filial.empresa, nome=f'Perfil {n}', is_admin=admin,
        )
        return Usuario.objects.create_user(
            email=f'u{n}@teste.local', nome='U', password='senha-de-teste-123',
            empresa=filial.empresa, perfil=perfil, filial=filial,
        )

    def _logar(self, usuario, filial):
        self.client.force_login(usuario)
        sessao = self.client.session
        sessao['filial_ativa_id'] = filial.pk
        sessao.save()

    def _cliente(self, nome, doc, *, lat=-5.79, lng=-35.21,
                 cidade='Natal', uf='RN', filial=None):
        from apps.cadastros.models import Cliente

        return Cliente.objects.create(
            filial=filial or self.filial, razao_social=nome, cpf_cnpj=doc,
            cidade=cidade, uf=uf, latitude=lat, longitude=lng, ativo=True,
        )

    def _venda_pdv(self, cliente, valor, *, filial=None, dias_atras=1, numero=None):
        from apps.pdv.models import VendaPDV

        BaseHeatmap._seq += 1
        filial = filial or self.filial
        return VendaPDV.objects.create(
            filial=filial, numero_venda=numero or BaseHeatmap._seq,
            cliente=cliente, usuario=self.usuario, status='finalizada',
            valor_total=Decimal(str(valor)),
            data_venda=timezone.now() - datetime.timedelta(days=dias_atras),
        )

    def _pagamento(self, venda, valor, *, movimenta_caixa=True, troco=0):
        """Pagamento com forma marcada (ou não) como receita."""
        from apps.financeiro.models import FormaPagamento
        from apps.pdv.models import PagamentoVendaPDV

        forma, _ = FormaPagamento.objects.get_or_create(
            empresa=venda.filial.empresa,
            descricao='Permuta' if not movimenta_caixa else 'Dinheiro',
            defaults={'movimenta_caixa': movimenta_caixa},
        )
        return PagamentoVendaPDV.objects.create(
            venda_pdv=venda, forma_pagamento=forma,
            valor=Decimal(str(valor)), troco=Decimal(str(troco)),
        )

    def _pedido_b2b(self, cliente, valor, *, filial=None, dias_atras=1,
                    representante=None, status=None):
        from apps.vendas.models import PedidoVenda

        BaseHeatmap._seq += 1
        return PedidoVenda.objects.create(
            filial=filial or self.filial, numero_pedido=f'P{BaseHeatmap._seq}',
            cliente=cliente, usuario=self.usuario, representante=representante,
            status=status or PedidoVenda.Status.FATURADO,
            valor_total=Decimal(str(valor)),
            data_emissao=timezone.now() - datetime.timedelta(days=dias_atras),
        )

    def _produto(self, descricao='Banana'):
        from apps.produtos.models import Produto, UnidadeMedida

        BaseHeatmap._seq += 1
        un, _ = UnidadeMedida.objects.get_or_create(
            empresa=self.filial.empresa, sigla='KG',
            defaults={'descricao': 'Quilo'},
        )
        return Produto.objects.create(
            filial=self.filial, descricao=f'{descricao} {BaseHeatmap._seq}',
            unidade_medida=un, ncm='08039000',
        )

    def _item_b2b(self, pedido, quantidade, valor_unitario=10):
        from apps.vendas.models import ItemPedidoVenda

        q = Decimal(str(quantidade))
        vu = Decimal(str(valor_unitario))
        return ItemPedidoVenda.objects.create(
            pedido=pedido, produto=self._produto(), quantidade=q,
            valor_unitario=vu, valor_bruto=q * vu, valor_total=q * vu,
        )

    def _pontos(self, **kwargs):
        from apps.mapas.services import HeatmapService

        kwargs.setdefault('filial', self.filial)
        return HeatmapService.pontos(**kwargs)


class ReceitaTests(BaseHeatmap):
    def test_soma_o_valor_das_vendas_por_cliente(self):
        c = self._cliente('A', '1')
        self._venda_pdv(c, 100)
        self._venda_pdv(c, 50)

        d = self._pontos(metrica='receita')

        self.assertEqual(d['total'], 150.0)
        self.assertEqual(d['locais'], 1)

    def test_permuta_nao_conta_como_receita(self):
        """A regra do resto do ERP: Doacao/Permuta dao baixa mas nao sao receita."""
        c = self._cliente('A', '1')
        venda = self._venda_pdv(c, 100)
        self._pagamento(venda, 100, movimenta_caixa=False)

        self.assertEqual(self._pontos(metrica='receita')['total'], 0.0)

    def test_permuta_parcial_desconta_so_a_parte_nao_contabilizada(self):
        c = self._cliente('A', '1')
        venda = self._venda_pdv(c, 100)
        self._pagamento(venda, 60, movimenta_caixa=True)
        self._pagamento(venda, 40, movimenta_caixa=False)

        self.assertEqual(self._pontos(metrica='receita')['total'], 60.0)

    def test_permuta_nao_mexe_na_contagem_de_pedidos(self):
        """O pedido aconteceu; o que nao houve foi dinheiro."""
        c = self._cliente('A', '1')
        venda = self._venda_pdv(c, 100)
        self._pagamento(venda, 100, movimenta_caixa=False)

        self.assertEqual(self._pontos(metrica='pedidos')['total'], 1.0)


class DuasOrigensTests(BaseHeatmap):
    """
    Pedido B2B e venda de PDV somam no mesmo ponto.

    Usar so uma das tabelas mostraria metade do faturamento -- e ninguem
    notaria olhando o mapa, porque ele continuaria plausivel.
    """

    def test_soma_pedido_b2b_com_venda_de_pdv(self):
        c = self._cliente('A', '1')
        self._pedido_b2b(c, 300)
        self._venda_pdv(c, 200)

        d = self._pontos(metrica='receita')
        self.assertEqual(d['total'], 500.0)
        self.assertEqual(d['locais'], 1)

    def test_pedido_b2b_sozinho_ja_acende_o_ponto(self):
        c = self._cliente('A', '1')
        self._pedido_b2b(c, 300)

        self.assertEqual(self._pontos(metrica='receita')['total'], 300.0)

    def test_pedido_em_rascunho_nao_conta(self):
        from apps.vendas.models import PedidoVenda

        c = self._cliente('A', '1')
        self._pedido_b2b(c, 300, status=PedidoVenda.Status.RASCUNHO)

        self.assertEqual(self._pontos(metrica='receita')['total'], 0.0)

    def test_pedidos_conta_as_duas_origens(self):
        c = self._cliente('A', '1')
        self._pedido_b2b(c, 300)
        self._venda_pdv(c, 200)

        self.assertEqual(self._pontos(metrica='pedidos')['total'], 2.0)

    def test_filtro_de_representante_mantem_so_o_pedido_dele(self):
        from apps.cadastros.models import Representante

        rep = Representante.objects.create(filial=self.filial, nome='Joao')
        outro = Representante.objects.create(filial=self.filial, nome='Maria')
        c = self._cliente('A', '1')
        self._pedido_b2b(c, 300, representante=rep)
        self._pedido_b2b(c, 700, representante=outro)

        d = self._pontos(metrica='receita', representante_id=rep.pk)
        self.assertEqual(d['total'], 300.0)


class MetricasTests(BaseHeatmap):
    def test_pedidos_conta_vendas(self):
        c = self._cliente('A', '1')
        self._venda_pdv(c, 10)
        self._venda_pdv(c, 20)
        self._venda_pdv(c, 30)

        self.assertEqual(self._pontos(metrica='pedidos')['total'], 3.0)

    def test_clientes_pesa_um_por_cadastro_independente_do_valor(self):
        grande = self._cliente('GRANDE', '1', lat=-5.79)
        pequeno = self._cliente('PEQUENO', '2', lat=-5.80)
        self._venda_pdv(grande, 100000)
        self._venda_pdv(pequeno, 1)

        d = self._pontos(metrica='clientes')

        self.assertEqual(d['total'], 2.0)
        self.assertEqual(sorted(p[2] for p in d['pontos']), [1.0, 1.0])

    def test_volume_soma_a_quantidade_dos_itens(self):
        c = self._cliente('A', '1')
        pedido = self._pedido_b2b(c, 100)
        self._item_b2b(pedido, 12)
        self._item_b2b(pedido, 8)

        self.assertEqual(self._pontos(metrica='volume')['total'], 20.0)

    def test_volume_nao_multiplica_pedidos_com_varios_itens(self):
        """
        O join com itens repete a linha do pedido. Somar quantidade esta certo,
        mas se algum dia isso virar Count o numero dobraria calado.
        """
        c = self._cliente('A', '1')
        p1 = self._pedido_b2b(c, 100)
        self._item_b2b(p1, 5)
        self._item_b2b(p1, 5)
        p2 = self._pedido_b2b(c, 100)
        self._item_b2b(p2, 10)

        self.assertEqual(self._pontos(metrica='volume')['total'], 20.0)
        self.assertEqual(self._pontos(metrica='pedidos')['total'], 2.0)

    def test_metrica_desconhecida_cai_em_receita(self):
        self.assertEqual(self._pontos(metrica='xpto')['metrica'], 'receita')

    def test_cliente_sem_venda_nao_vira_ponto(self):
        self._cliente('SEM VENDA', '1')
        self.assertEqual(self._pontos(metrica='receita')['pontos'], [])


class NormalizacaoTests(BaseHeatmap):
    def test_peso_vai_de_0_a_1_contra_o_maior_do_recorte(self):
        """
        O leaflet.heat satura acima de 1: mandar reais crus pintaria tudo de
        vermelho e o mapa perderia qualquer gradacao.
        """
        maior = self._cliente('MAIOR', '1', lat=-5.79)
        menor = self._cliente('MENOR', '2', lat=-5.80)
        self._venda_pdv(maior, 1000)
        self._venda_pdv(menor, 250)

        d = self._pontos(metrica='receita')
        pesos = sorted(p[2] for p in d['pontos'])

        self.assertEqual(pesos, [0.25, 1.0])
        self.assertEqual(d['maximo'], 1000.0)

    def test_valores_absolutos_sobrevivem_a_normalizacao(self):
        """A legenda precisa dizer o que a cor vale."""
        c = self._cliente('A', '1')
        self._venda_pdv(c, 777)

        d = self._pontos(metrica='receita')
        self.assertEqual(d['total'], 777.0)
        self.assertEqual(d['maximo'], 777.0)


class FiltrosTests(BaseHeatmap):
    def test_filtra_por_cidade(self):
        natal = self._cliente('NATAL', '1', cidade='Natal', lat=-5.79)
        mossoro = self._cliente('MOSSORO', '2', cidade='Mossoró', lat=-5.19)
        self._venda_pdv(natal, 100)
        self._venda_pdv(mossoro, 500)

        d = self._pontos(metrica='receita', cidade='Natal')
        self.assertEqual(d['total'], 100.0)

    def test_filtra_por_uf(self):
        rn = self._cliente('RN', '1', uf='RN', lat=-5.79)
        pb = self._cliente('PB', '2', uf='PB', lat=-7.11)
        self._venda_pdv(rn, 100)
        self._venda_pdv(pb, 900)

        self.assertEqual(self._pontos(metrica='receita', uf='PB')['total'], 900.0)

    def test_filtra_por_periodo(self):
        c = self._cliente('A', '1')
        self._venda_pdv(c, 100, dias_atras=2)
        self._venda_pdv(c, 900, dias_atras=400)

        hoje = timezone.localdate()
        d = self._pontos(
            metrica='receita', inicio=hoje - datetime.timedelta(days=30), fim=hoje,
        )
        self.assertEqual(d['total'], 100.0)

    def test_filtra_por_filial(self):
        outra = self._filial_extra(self.filial.empresa, 'Loja 2', '11222333000262')
        aqui = self._cliente('AQUI', '1', lat=-5.79)
        la = self._cliente('LA', '2', lat=-5.80, filial=outra)
        self._venda_pdv(aqui, 100)
        self._venda_pdv(la, 700, filial=outra)

        # Matriz vê as duas...
        self.assertEqual(self._pontos(metrica='receita')['total'], 800.0)
        # ...e o filtro estreita para uma.
        self.assertEqual(
            self._pontos(metrica='receita', filial_id=outra.pk)['total'], 700.0)

    def test_filial_de_outra_empresa_no_filtro_nao_vaza(self):
        """Trocar o id na URL nao pode virar faturamento alheio."""
        outra_empresa = self._empresa('Beta', '99888777000166')
        alheio = self._cliente('ALHEIO', '9', filial=outra_empresa)
        self._venda_pdv(alheio, 5000, filial=outra_empresa)

        d = self._pontos(metrica='receita', filial_id=outra_empresa.pk)
        self.assertEqual(d['total'], 0.0)

    def test_representante_deixa_o_balcao_de_fora(self):
        """
        Venda de balcao nao guarda representante; incluí-la atribuiria a um
        vendedor faturamento que nao e dele.
        """
        from apps.cadastros.models import Representante

        rep = Representante.objects.create(filial=self.filial, nome='Joao')
        c = self._cliente('A', '1')
        self._venda_pdv(c, 100)

        d = self._pontos(metrica='receita', representante_id=rep.pk)
        self.assertEqual(d['total'], 0.0)


class EscopoTests(BaseHeatmap):
    def test_cliente_de_outra_empresa_nao_entra(self):
        outra = self._empresa('Beta', '99888777000166')
        meu = self._cliente('MEU', '1', lat=-5.79)
        alheio = self._cliente('ALHEIO', '9', lat=-5.80, filial=outra)
        self._venda_pdv(meu, 100)
        self._venda_pdv(alheio, 900, filial=outra)

        d = self._pontos(metrica='receita')
        self.assertEqual(d['total'], 100.0)
        self.assertEqual(d['locais'], 1)

    def test_conta_quem_ficou_de_fora_por_falta_de_coordenada(self):
        """Um mapa incompleto lido como o todo levaria a conclusao errada."""
        from apps.cadastros.models import Cliente

        com = self._cliente('COM GEO', '1')
        sem = Cliente.objects.create(
            filial=self.filial, razao_social='SEM GEO', cpf_cnpj='2',
            cidade='Natal', uf='RN', ativo=True,
        )
        self._venda_pdv(com, 100)
        self._venda_pdv(sem, 100)

        d = self._pontos(metrica='receita')
        self.assertEqual(d['locais'], 1)
        self.assertEqual(d['sem_coordenada'], 1)


class ApiTests(BaseHeatmap):
    def test_endpoint_devolve_os_pontos(self):
        c = self._cliente('A', '1')
        self._venda_pdv(c, 100)

        d = self.client.get(reverse('mapas:api-heatmap'),
                            {'metrica': 'receita'}).json()

        self.assertEqual(d['metrica'], 'receita')
        self.assertEqual(len(d['pontos']), 1)
        self.assertEqual(d['pontos'][0][2], 1.0)

    def test_data_invalida_nao_derruba_a_consulta(self):
        c = self._cliente('A', '1')
        self._venda_pdv(c, 100)

        resp = self.client.get(reverse('mapas:api-heatmap'),
                               {'de': 'ontem', 'ate': '??'})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['total'], 100.0)

    def test_filtros_listam_so_o_escopo(self):
        outra = self._empresa('Beta', '99888777000166')
        self._cliente('MEU', '1', cidade='Natal')
        self._cliente('ALHEIO', '9', cidade='Recife', filial=outra)

        d = self.client.get(reverse('mapas:api-heatmap-filtros')).json()

        self.assertIn('Natal', d['cidades'])
        self.assertNotIn('Recife', d['cidades'])
        self.assertEqual(len(d['metricas']), 4)

    def test_exige_autenticacao(self):
        self.client.logout()
        resp = self.client.get(reverse('mapas:api-heatmap'))
        self.assertIn(resp.status_code, (302, 401, 403))


class ZonaTests(BaseHeatmap):
    """
    Quadrantes N/S/L/O calculados a partir da coordenada.

    O centro e a media dos clientes do escopo. Aqui os pontos sao montados em
    volta de (-5.79, -35.21) para o centro cair ali e as zonas ficarem obvias.
    """

    def _cenario(self):
        """Quatro clientes, um em cada direcao, simetricos em volta do centro."""
        pontos = {
            'NORTE': (-5.70, -35.21),
            'SUL':   (-5.88, -35.21),
            'LESTE': (-5.79, -35.12),
            'OESTE': (-5.79, -35.30),
        }
        for i, (nome, (lat, lng)) in enumerate(pontos.items()):
            c = self._cliente(nome, str(i), lat=lat, lng=lng)
            self._venda_pdv(c, 100)

    def _nomes(self, **kw):
        from apps.cadastros.models import Cliente

        d = self._pontos(metrica='receita', **kw)
        coords = {(round(p[0], 4), round(p[1], 4)) for p in d['pontos']}
        return sorted(
            c.razao_social for c in Cliente.objects.filter(filial=self.filial)
            if c.latitude and (round(c.latitude, 4), round(c.longitude, 4)) in coords
        )

    def test_cada_zona_traz_so_o_seu_quadrante(self):
        self._cenario()

        self.assertEqual(self._nomes(zona='norte'), ['NORTE'])
        self.assertEqual(self._nomes(zona='sul'), ['SUL'])
        self.assertEqual(self._nomes(zona='leste'), ['LESTE'])
        self.assertEqual(self._nomes(zona='oeste'), ['OESTE'])

    def test_as_quatro_zonas_somam_a_base_inteira(self):
        """
        Com metades simples em vez de cunhas, cada ponto cairia em duas zonas
        e a soma daria o dobro. Este teste e o que trava isso.
        """
        self._cenario()

        total = self._pontos(metrica='receita')['total']
        soma = sum(
            self._pontos(metrica='receita', zona=z)['total']
            for z in ('norte', 'sul', 'leste', 'oeste')
        )
        self.assertEqual(soma, total)

    def test_zona_desconhecida_nao_filtra_nada(self):
        self._cenario()
        self.assertEqual(self._pontos(metrica='receita', zona='nordeste')['total'],
                         self._pontos(metrica='receita')['total'])

    def test_base_sem_coordenada_nenhuma_nao_quebra(self):
        """Sem centro nao da para dividir; a consulta nao pode estourar."""
        d = self._pontos(metrica='receita', zona='norte')
        self.assertEqual(d['total'], 0.0)

    def test_zona_combina_com_periodo(self):
        self._cenario()
        d = self._pontos(
            metrica='receita', zona='norte',
            inicio=timezone.localdate() - datetime.timedelta(days=30),
            fim=timezone.localdate(),
        )
        self.assertEqual(d['total'], 100.0)


class BairroTests(BaseHeatmap):
    def test_filtra_por_bairro(self):
        from apps.cadastros.models import Cliente

        a = self._cliente('PONTA NEGRA', '1', lat=-5.88)
        b = self._cliente('ALECRIM', '2', lat=-5.79)
        Cliente.objects.filter(pk=a.pk).update(bairro='Ponta Negra')
        Cliente.objects.filter(pk=b.pk).update(bairro='Alecrim')
        self._venda_pdv(a, 300)
        self._venda_pdv(b, 700)

        self.assertEqual(
            self._pontos(metrica='receita', bairro='Ponta Negra')['total'], 300.0)

    def test_bairro_ignora_maiusculas(self):
        from apps.cadastros.models import Cliente

        c = self._cliente('A', '1')
        Cliente.objects.filter(pk=c.pk).update(bairro='Ponta Negra')
        self._venda_pdv(c, 300)

        self.assertEqual(
            self._pontos(metrica='receita', bairro='PONTA NEGRA')['total'], 300.0)

    def test_bairro_entra_na_contagem_de_sem_coordenada(self):
        """O aviso tem de acompanhar o recorte, senao vira um numero solto."""
        from apps.cadastros.models import Cliente

        sem = Cliente.objects.create(
            filial=self.filial, razao_social='SEM GEO', cpf_cnpj='9',
            cidade='Natal', uf='RN', bairro='Alecrim', ativo=True,
        )
        self._venda_pdv(sem, 100)

        self.assertEqual(
            self._pontos(metrica='receita', bairro='Alecrim')['sem_coordenada'], 1)
        self.assertEqual(
            self._pontos(metrica='receita', bairro='Tirol')['sem_coordenada'], 0)

    def test_lista_de_bairros_so_traz_o_escopo(self):
        from apps.cadastros.models import Cliente

        outra = self._empresa('Beta', '99888777000166')
        meu = self._cliente('MEU', '1')
        alheio = self._cliente('ALHEIO', '9', filial=outra)
        Cliente.objects.filter(pk=meu.pk).update(bairro='Alecrim')
        Cliente.objects.filter(pk=alheio.pk).update(bairro='Boa Viagem')

        d = self.client.get(reverse('mapas:api-heatmap-filtros')).json()

        self.assertIn('Alecrim', d['bairros'])
        self.assertNotIn('Boa Viagem', d['bairros'])
        self.assertEqual(len(d['zonas']), 4)


class TerritorioFiltroTests(BaseHeatmap):
    def test_filtra_pelos_clientes_do_territorio_desenhado(self):
        from apps.cadastros.models import Praca
        from apps.mapas.models import ClienteTerritorio

        praca = Praca.objects.create(filial=self.filial, nome='Zona Sul')
        dentro = self._cliente('DENTRO', '1', lat=-5.88)
        fora = self._cliente('FORA', '2', lat=-5.70)
        ClienteTerritorio.objects.create(praca=praca, cliente=dentro)
        self._venda_pdv(dentro, 400)
        self._venda_pdv(fora, 600)

        d = self._pontos(metrica='receita', praca_id=praca.pk)
        self.assertEqual(d['total'], 400.0)

    def test_territorio_de_outra_empresa_nao_vaza(self):
        from apps.cadastros.models import Praca
        from apps.mapas.models import ClienteTerritorio

        outra = self._empresa('Beta', '99888777000166')
        praca_alheia = Praca.objects.create(filial=outra, nome='Alheia')
        alheio = self._cliente('ALHEIO', '9', filial=outra)
        ClienteTerritorio.objects.create(praca=praca_alheia, cliente=alheio)
        self._venda_pdv(alheio, 5000, filial=outra)

        d = self._pontos(metrica='receita', praca_id=praca_alheia.pk)
        self.assertEqual(d['total'], 0.0)

    def test_so_lista_praca_com_cliente_atribuido(self):
        """Praca sem poligono nao tem como dizer quem esta dentro."""
        from apps.cadastros.models import Praca
        from apps.mapas.models import ClienteTerritorio

        com = Praca.objects.create(filial=self.filial, nome='Com Poligono')
        Praca.objects.create(filial=self.filial, nome='Sem Poligono')
        ClienteTerritorio.objects.create(praca=com, cliente=self._cliente('A', '1'))

        d = self.client.get(reverse('mapas:api-heatmap-filtros')).json()
        nomes = [t['nome'] for t in d['territorios']]

        self.assertEqual(nomes, ['Com Poligono'])
