"""
Comando de uso único: apaga todos os DocumentoFiscal (NFC-e / NF-e)
e LogIntegracaoFiscal do banco e reseta proximo_numero_nfce na(s) filial(is).

Uso:
    python manage.py limpar_documentos_fiscais
    python manage.py limpar_documentos_fiscais --dry-run   # apenas mostra o que seria apagado
"""
from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Apaga todos os DocumentoFiscal NFC-e/NF-e, LogIntegracaoFiscal e reseta numeracao."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas exibe o que seria apagado, sem deletar.",
        )

    def handle(self, *args, **options):
        dry = options["dry_run"]

        from apps.financeiro.models.fiscal import DocumentoFiscal, LogIntegracaoFiscal
        from apps.core.models.empresa import Filial

        docs = DocumentoFiscal.objects.filter(tipo_documento__in=["nfce", "nfe"])
        logs = LogIntegracaoFiscal.objects.all()
        filiais = Filial.objects.filter(proximo_numero_nfce__gt=1)

        self.stdout.write(f"DocumentoFiscal a apagar : {docs.count()}")
        self.stdout.write(f"LogIntegracaoFiscal a apagar: {logs.count()}")
        self.stdout.write(f"Filiais com numero > 1   : {filiais.count()}")

        if dry:
            self.stdout.write(self.style.WARNING("--dry-run: nada foi alterado."))
            return

        with transaction.atomic():
            log_del, _ = logs.delete()
            doc_del, _ = docs.delete()
            for f in filiais:
                f.proximo_numero_nfce = 1
                f.save(update_fields=["proximo_numero_nfce"])

        self.stdout.write(self.style.SUCCESS(
            f"Concluido: {doc_del} documentos apagados, "
            f"{log_del} logs apagados, "
            f"{filiais.count()} filiais resetadas para numero 1."
        ))
