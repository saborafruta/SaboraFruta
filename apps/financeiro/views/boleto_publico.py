"""Segunda via pública do boleto por URL-capacidade não enumerável."""
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from apps.financeiro.models import Boleto
from apps.financeiro.services.boleto_bradesco import gerar_pdf_boleto


def _privado(response):
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _buscar(token):
    return get_object_or_404(
        Boleto.objects.select_related(
            "filial__empresa", "conta_bancaria", "conta_receber__cliente",
        ),
        public_token=token,
    )


@require_GET
def visualizar(request, token):
    boleto = _buscar(token)
    return _privado(render(request, "financeiro/boletos/publico.html", {
        "boleto": boleto,
        "pdf_url": reverse("boleto_publico:pdf", args=[token]),
    }))


@require_GET
def baixar_pdf(request, token):
    boleto = _buscar(token)
    response = HttpResponse(gerar_pdf_boleto(boleto), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="boleto-{boleto.seu_numero or boleto.pk}.pdf"'
    return _privado(response)
