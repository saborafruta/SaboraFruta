import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Empresa
from apps.financeiro.models import PlanoContabil


DEFAULT_DATA_FILE = (
    Path(__file__).resolve().parents[2]
    / "data"
    / "plano_contabil_eureka_2026.json"
)


class Command(BaseCommand):
    help = "Importa o plano contábil oficial extraído da Relação de Contas."

    def add_arguments(self, parser):
        parser.add_argument("--empresa-cnpj", required=True)
        parser.add_argument("--arquivo", default=str(DEFAULT_DATA_FILE))

    @transaction.atomic
    def handle(self, *args, **options):
        cnpj = "".join(filter(str.isdigit, options["empresa_cnpj"]))
        try:
            empresa = Empresa.objects.get(cnpj=cnpj)
        except Empresa.DoesNotExist as exc:
            raise CommandError(f"Empresa com CNPJ {cnpj} não encontrada.") from exc

        arquivo = Path(options["arquivo"])
        if not arquivo.exists():
            raise CommandError(f"Arquivo não encontrado: {arquivo}")

        payload = json.loads(arquivo.read_text(encoding="utf-8"))
        contas = payload.get("contas", [])
        if len(contas) != payload.get("total"):
            raise CommandError("Quantidade de contas diverge do total informado no arquivo.")

        classificacoes_existentes = set(
            PlanoContabil.objects.filter(empresa=empresa).values_list(
                "classificacao", flat=True
            )
        )
        origem = f"{payload.get('fonte', 'Relação de Contas')} - emissão {payload.get('emitido_em', '')}"

        objetos_importacao = []
        for item in contas:
            objetos_importacao.append(PlanoContabil(
                empresa=empresa,
                classificacao=item["classificacao"],
                codigo_referencia=item["codigo_referencia"],
                tipo_conta=item["tipo_conta"],
                descricao=item["descricao"],
                codigo_dre=item["codigo_dre"],
                data_inicio=item["data_inicio"],
                nivel=item["nivel"],
                ordem=item["ordem"],
                pagina_origem=item["pagina_origem"],
                origem=origem,
                ativo=item["ativo"],
            ))

        PlanoContabil.objects.bulk_create(
            objetos_importacao,
            update_conflicts=True,
            unique_fields=["empresa", "classificacao"],
            update_fields=[
                "codigo_referencia",
                "tipo_conta",
                "descricao",
                "codigo_dre",
                "data_inicio",
                "nivel",
                "ordem",
                "pagina_origem",
                "origem",
                "ativo",
            ],
        )

        objetos = {
            conta.classificacao: conta
            for conta in PlanoContabil.objects.filter(empresa=empresa)
        }
        contas_com_pai = []
        for item in contas:
            conta = objetos[item["classificacao"]]
            pai = objetos.get(item["classificacao_pai"])
            if conta.conta_pai_id != (pai.id if pai else None):
                conta.conta_pai = pai
                contas_com_pai.append(conta)
        if contas_com_pai:
            PlanoContabil.objects.bulk_update(contas_com_pai, ["conta_pai"])

        criadas = len(set(objetos) - classificacoes_existentes)
        atualizadas = len(contas) - criadas

        importadas = PlanoContabil.objects.filter(
            empresa=empresa,
            classificacao__in=[item["classificacao"] for item in contas],
        )
        sem_pai = importadas.filter(nivel__gt=1, conta_pai__isnull=True).count()
        if importadas.count() != len(contas) or sem_pai:
            raise CommandError(
                f"Validação falhou: importadas={importadas.count()}, sem_pai={sem_pai}."
            )

        self.stdout.write(self.style.SUCCESS(
            f"Plano contábil importado para {empresa}: "
            f"{criadas} criadas, {atualizadas} atualizadas, {importadas.count()} total."
        ))
