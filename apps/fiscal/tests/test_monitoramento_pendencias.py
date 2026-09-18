from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.core.models import Empresa, Filial, Notificacao, PerfilAcesso, Usuario
from apps.financeiro.constants.enums import StatusDocumentoFiscal, TipoDocumentoFiscal
from apps.financeiro.models import DocumentoFiscal
from apps.fiscal.tasks import _monitorar_pendencias_fiscais_banco_atual


class MonitoramentoPendenciasFiscaisTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        empresa = Empresa.objects.create(
            razao_social="Empresa Fiscal LTDA",
            nome_fantasia="Empresa Fiscal",
            cnpj="81345678000191",
            regime_tributario=Empresa.RegimeTributario.SIMPLES_NACIONAL,
            codigo_regime_tributario=1,
        )
        cls.filial = Filial.objects.create(
            empresa=empresa,
            razao_social="Filial Fiscal",
            nome_fantasia="Loja Fiscal",
            cnpj="81345678000192",
            uf="RN",
        )
        perfil = PerfilAcesso.objects.create(empresa=empresa, nome="Fiscal", is_admin=True)
        cls.usuario = Usuario.objects.create_user(
            email="fiscal-watchdog@example.com",
            nome="Fiscal Watchdog",
            password="teste1234",
            empresa=empresa,
            filial=cls.filial,
            perfil=perfil,
        )

    def test_alerta_nfce_processando_e_desativa_quando_autorizada(self):
        documento = DocumentoFiscal.objects.create(
            filial=self.filial,
            tipo_documento=TipoDocumentoFiscal.NFCE,
            origem_tipo="venda_pdv",
            origem_id=77,
            numero=7,
            serie=1,
            emitente_cnpj=self.filial.cnpj,
            destinatario_snapshot={"nome": "Consumidor Final"},
            data_emissao=timezone.now() - timedelta(minutes=10),
            status=StatusDocumentoFiscal.PROCESSANDO,
            valor_total=Decimal("10.00"),
            usuario=self.usuario,
            em_contingencia=True,
        )
        DocumentoFiscal.objects.filter(pk=documento.pk).update(
            updated_at=timezone.now() - timedelta(minutes=6),
        )

        _monitorar_pendencias_fiscais_banco_atual()
        alerta = Notificacao.objects.get(
            referencia_tipo="nfce_pendente",
            referencia_id=str(documento.pk),
        )
        self.assertTrue(alerta.ativa)
        self.assertIn("contingência", alerta.titulo)

        documento.status = StatusDocumentoFiscal.AUTORIZADA
        documento.save(update_fields=["status"])
        _monitorar_pendencias_fiscais_banco_atual()
        alerta.refresh_from_db()
        self.assertFalse(alerta.ativa)
