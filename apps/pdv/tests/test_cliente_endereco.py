from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase

from apps.pdv.views.pdv import _cliente_endereco_preferencial


class ClienteEnderecoPreferencialTests(SimpleTestCase):
    def test_completa_endereco_principal_com_endereco_adicional(self):
        extra = SimpleNamespace(
            endereco="Rua Importada",
            numero="123",
            bairro="Centro",
            complemento="Sala 2",
            cidade="Natal",
            uf="RN",
            cep="59000000",
        )
        enderecos = Mock()
        enderecos.filter.return_value.order_by.return_value.first.return_value = extra
        cliente = SimpleNamespace(
            endereco="",
            numero="",
            bairro="",
            complemento="",
            cidade="",
            uf="",
            cep="",
            enderecos=enderecos,
        )

        endereco = _cliente_endereco_preferencial(cliente)

        self.assertEqual(endereco["rua"], "Rua Importada")
        self.assertEqual(endereco["numero"], "123")
        self.assertEqual(endereco["bairro"], "Centro")
        self.assertEqual(endereco["cidade"], "Natal")
        self.assertEqual(endereco["uf"], "RN")
        self.assertEqual(endereco["cep"], "59000000")

    def test_endereco_adicional_nao_apaga_dado_principal(self):
        extra = SimpleNamespace(
            endereco="",
            numero="",
            bairro="",
            complemento="",
            cidade="",
            uf="",
            cep="",
        )
        enderecos = Mock()
        enderecos.filter.return_value.order_by.return_value.first.return_value = extra
        cliente = SimpleNamespace(
            endereco="Avenida Principal",
            numero="10",
            bairro="Lagoa Nova",
            complemento="",
            cidade="Natal",
            uf="RN",
            cep="59000000",
            enderecos=enderecos,
        )

        endereco = _cliente_endereco_preferencial(cliente)

        self.assertEqual(endereco["rua"], "Avenida Principal")
        self.assertEqual(endereco["bairro"], "Lagoa Nova")
