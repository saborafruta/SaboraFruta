from io import BytesIO
import shutil
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.template.loader import get_template
from django.test import RequestFactory, TestCase, override_settings
from PIL import Image

from apps.cadastros.models import Funcionario
from apps.cadastros.views.funcionario import FuncionarioCreateView
from apps.core.models import Empresa, Filial


TEMP_MEDIA_ROOT = tempfile.mkdtemp(prefix="ited-funcionario-foto-")


def foto_png(nome="profissional.png"):
    arquivo = BytesIO()
    Image.new("RGB", (80, 80), "#2563eb").save(arquivo, format="PNG")
    return SimpleUploadedFile(nome, arquivo.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=TEMP_MEDIA_ROOT)
class FuncionarioFotoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        empresa = Empresa.objects.create(
            razao_social="Empresa Foto LTDA",
            nome_fantasia="Empresa Foto",
            cnpj="88776655000110",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=empresa,
            razao_social="Empresa Foto",
            nome_fantasia="Empresa Foto",
            cnpj="88776655000111",
            uf="CE",
        )

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEMP_MEDIA_ROOT, ignore_errors=True)

    @patch("apps.cadastros.views.funcionario.messages.success")
    def test_cadastro_salva_foto_enviada(self, _mensagem):
        request = RequestFactory().post(
            "/cadastros/funcionarios/novo/",
            {"nome": "Ana Fotografa", "salario_base": "0", "foto": foto_png()},
        )
        request.filial_ativa = self.filial
        request.user = None

        response = FuncionarioCreateView().post(request)

        self.assertEqual(response.status_code, 302)
        funcionario = Funcionario.objects.get(nome="Ana Fotografa")
        self.assertTrue(funcionario.foto.name.startswith("funcionarios/fotos/"))

    def test_formulario_aceita_upload_e_exibe_previa(self):
        fonte = get_template("cadastros/funcionario/form.html").template.source

        self.assertIn('enctype="multipart/form-data"', fonte)
        self.assertIn("Foto do funcionario", fonte)
        self.assertIn("funcionario.foto.url", fonte)
