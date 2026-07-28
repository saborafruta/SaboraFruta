from django.test import SimpleTestCase

from apps.cadastros.forms import MotoristaForm, VeiculoForm


class MotoristaFormTests(SimpleTestCase):
    def test_exige_cpf_valido(self):
        form = MotoristaForm(data={"nome": "Motorista Teste", "cpf": "123"})

        self.assertFalse(form.is_valid())
        self.assertIn("cpf", form.errors)

    def test_normaliza_cpf(self):
        form = MotoristaForm(
            data={"nome": "Motorista Teste", "cpf": "123.456.789-01", "ativo": True}
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["cpf"], "12345678901")


class VeiculoFormTests(SimpleTestCase):
    def dados_validos(self):
        return {
            "placa": "ABC-1D23",
            "uf_placa": "RN",
            "tipo_rodado": "Van",
            "tipo_carroceria": "Fechada",
            "tara": "1500.000",
            "ativo": True,
        }

    def test_exige_campos_do_mdfe(self):
        form = VeiculoForm(data={"placa": "ABC1D23"})

        self.assertFalse(form.is_valid())
        for campo in ("uf_placa", "tipo_rodado", "tipo_carroceria", "tara"):
            self.assertIn(campo, form.errors)

    def test_normaliza_placa_e_aceita_dados_validos(self):
        form = VeiculoForm(data=self.dados_validos())

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["placa"], "ABC1D23")

    def test_rejeita_tara_zerada(self):
        dados = self.dados_validos()
        dados["tara"] = "0"
        form = VeiculoForm(data=dados)

        self.assertFalse(form.is_valid())
        self.assertIn("tara", form.errors)
