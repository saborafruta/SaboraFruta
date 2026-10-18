import Foundation
import Testing
@testable import ITEDForcaVendas

@Test func parsesBrazilianDecimalInput() {
    #expect(Decimal.parse("12,50") == Decimal(string: "12.50"))
}

@Test func checkoutLineCalculatesTotal() {
    let product = Product(
        id: 1, descricao: "Produto", codigo: "1", unidade: "UN",
        preco: "7.50", precoOrigem: "padrao", categoria: "Geral",
        destaque: false, estoqueDisponivel: "10.000", controlaLote: false,
        podeVender: true, bloqueios: [], estoques: []
    )
    let line = CheckoutLine(product: product, quantity: 3)
    #expect(line.total == Decimal(string: "22.50"))
}

@Test func workContextHasStableIdentity() {
    let context = WorkContext(
        tenantAlias: "cliente_a", empresaId: 3, empresaNome: "Empresa",
        filialId: 7, filialNome: "Matriz", filialCnpj: "",
        podeCriarPedido: true, podeSeparar: true, podeCheckout: true
    )
    #expect(context.id == "cliente_a-7")
}
