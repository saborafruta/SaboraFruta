"""
Que módulo cada vertical concede.

O QUE ESTES TESTES CERCAM:

  · O SALÃO E' DA PADARIA. Food Service (mesa, comanda, KDS, cardápio) só
    aparece para o vertical Padarias. Fábrica atende pedido, não mesa — e a
    indústria de polpa recebia um menu inteiro que nunca ia abrir;

  · ESCONDER DO MENU NÃO E' BARRAR. A mesma resposta serve ao sidebar e ao
    middleware (`modulos_ativos`), senão o item some da lista e a URL
    continua respondendo;

  · A LIBERAÇÃO MANUAL CONTINUA VALENDO. `modulos_extras` é a porta de quem
    não se encaixa no vertical mas usa o módulo — a regra de segmento não
    pode fechá-la.
"""
from django.test import TestCase

from apps.core.constants import segmentos as seg
from apps.core.models import Empresa, Filial
from apps.core.services.modulos import (
    modulo_da_url, modulos_ativos, modulos_disponiveis,
)


class ModulosPorSegmentoTests(TestCase):

    def _empresa(self, segmento, extras=None, cnpj='95345678000191'):
        return Empresa.objects.create(
            razao_social='Empresa Segmento LTDA', nome_fantasia='Segmento',
            cnpj=cnpj, segmento=segmento, modulos_extras=extras or [],
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )

    def test_padaria_tem_food_service(self):
        empresa = self._empresa(seg.PADARIAS)

        self.assertIn('food_service', modulos_disponiveis(empresa))

    def test_industria_alimenticia_nao_tem_food_service(self):
        """Fábrica atende pedido, não mesa."""
        empresa = self._empresa(seg.INDUSTRIA_ALIMENTICIA)

        disponiveis = modulos_disponiveis(empresa)

        self.assertNotIn('food_service', disponiveis)
        self.assertIn('polpa', disponiveis)

    def test_polpa_de_frutas_nao_tem_food_service(self):
        empresa = self._empresa(seg.POLPA_FRUTAS)

        self.assertNotIn('food_service', modulos_disponiveis(empresa))

    def test_empresa_sem_segmento_fica_so_com_os_universais(self):
        empresa = self._empresa('')

        disponiveis = modulos_disponiveis(empresa)

        self.assertNotIn('food_service', disponiveis)
        self.assertIn('cadastros', disponiveis)

    def test_liberacao_manual_continua_valendo(self):
        """
        A porta de quem não se encaixa no vertical mas usa o módulo. Fechá-la
        junto com a regra de segmento tiraria o módulo de quem ja' trabalha
        com ele, sem aviso e sem caminho de volta.
        """
        empresa = self._empresa(seg.INDUSTRIA_ALIMENTICIA, extras=['food_service'])

        self.assertIn('food_service', modulos_disponiveis(empresa))

    def test_a_url_do_food_service_segue_a_mesma_regra(self):
        """Esconder do menu não basta: a URL precisa cair fora também."""
        empresa = self._empresa(seg.INDUSTRIA_ALIMENTICIA)
        filial = Filial.objects.create(
            empresa=empresa, razao_social='Fabrica', cnpj='95345678000272',
            uf='RN',
        )

        self.assertEqual(modulo_da_url('/food-service/mesas/'), 'food_service')
        self.assertNotIn('food_service', modulos_ativos(filial))
