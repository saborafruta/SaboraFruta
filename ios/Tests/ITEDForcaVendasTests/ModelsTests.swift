import Foundation
import Testing
@testable import ITEDForcaVendas

@Test func parsesBrazilianDecimalInput() {
    #expect(Decimal.parse("12,50") == Decimal(string: "12.50"))
}

@Test func checkoutLineCalculatesTotal() {
    let product = Product(
        id: 1, descricao: "Produto", codigo: "1", unidade: "UN",
        preco: "7.50", precoOrigem: "padrao", categoria: "Geral", categoriaId: 1,
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

@Test func orderDraftQuantityChangesKeepTheProductInTheOrder() {
    let product = Product(
        id: 42, descricao: "Produto do pedido", codigo: "42", unidade: "UN",
        preco: "5.00", precoOrigem: "padrao", categoria: "Geral", categoriaId: 1,
        destaque: false, estoqueDisponivel: "10.000", controlaLote: false,
        podeVender: true, bloqueios: [], estoques: []
    )
    var line = OrderDraftLine(product: product, quantity: 1)

    line.incrementQuantity()
    #expect(line.id == product.id)
    #expect(line.quantity == 2)

    line.decrementQuantity()
    #expect(line.id == product.id)
    #expect(line.quantity == 1)

    line.decrementQuantity()
    line.decrementQuantity()
    #expect(line.id == product.id)
    #expect(line.quantity == Decimal(string: "0.001"))
}

@Test func changingOrderQuantityByProductIDNeverRemovesTheLine() {
    let product = Product(
        id: 77, descricao: "Produto preservado", codigo: "77", unidade: "UN",
        preco: "8.00", precoOrigem: "padrao", categoria: "Geral", categoriaId: 1,
        destaque: false, estoqueDisponivel: "10.000", controlaLote: false,
        podeVender: true, bloqueios: [], estoques: []
    )
    var lines = [OrderDraftLine(product: product, quantity: 1)]

    lines.adjustQuantity(for: product.id, by: 1)
    #expect(lines.count == 1)
    #expect(lines.first?.id == product.id)
    #expect(lines.first?.quantity == 2)

    lines.adjustQuantity(for: product.id, by: -1)
    #expect(lines.count == 1)
    #expect(lines.first?.quantity == 1)
}
