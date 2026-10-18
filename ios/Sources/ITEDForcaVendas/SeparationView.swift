import SwiftUI

struct SeparationView: View {
    @Environment(AppModel.self) private var model
    @State private var orders: [SeparationOrder] = []

    var body: some View {
        List(orders) { order in
            NavigationLink { SeparationDetailView(order: order) } label: {
                VStack(alignment: .leading, spacing: 5) {
                    HStack { Text("Pedido #\(order.numeroPedido)").font(.headline); Spacer(); Text(order.prioridade).font(.caption.bold()) }
                    Text(order.clienteNome)
                    Text("\(order.itens.count) item(ns) • \(order.status)").font(.caption).foregroundStyle(.secondary)
                }.padding(.vertical, 4)
            }
        }
        .overlay { if orders.isEmpty && !model.isBusy { ContentUnavailableView("Sem pedidos para separar", systemImage: "shippingbox") } }
        .navigationTitle("Separação")
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        guard let session = model.session else { return }
        await model.perform { orders = try await model.api.separations(session: session); model.lastSync = Date() }
    }
}

private struct SeparationDraft: Identifiable {
    let id: Int
    let line: SeparationLine
    var depositID: Int
    var quantity: Decimal
    var batchID: Int?
}

private struct SeparationDetailView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let order: SeparationOrder
    @State private var drafts: [SeparationDraft] = []
    @State private var notes = ""

    var body: some View {
        Form {
            Section {
                LabeledContent("Cliente", value: order.clienteNome)
                LabeledContent("Pedido", value: "#\(order.numeroPedido)")
            }
            ForEach($drafts) { $draft in
                Section(draft.line.produtoDescricao) {
                    Text("Solicitado: \(draft.line.quantidadeSolicitada) \(draft.line.unidade)").font(.caption)
                    Picker("Depósito", selection: $draft.depositID) {
                        ForEach(draft.line.depositos) { deposit in
                            Text("\(deposit.nome) — \(deposit.disponivel)").tag(deposit.id)
                        }
                    }
                    TextField("Quantidade separada", value: $draft.quantity, format: .number.precision(.fractionLength(0...3))).keyboardType(.decimalPad)
                    if draft.line.controlaLote {
                        Picker("Lote", selection: $draft.batchID) {
                            Text("Selecione o lote").tag(nil as Int?)
                            ForEach(draft.line.lotesSugeridos) { batch in
                                Text("\(batch.numero) — \(batch.quantidade)").tag(Optional(batch.id))
                            }
                        }
                    } else {
                        Label("Produto sem controle de lote", systemImage: "checkmark.circle").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            Section("Observações") { TextField("Observações da separação", text: $notes, axis: .vertical).lineLimit(3...6) }
            Button { Task { await complete() } } label: { Text("Concluir separação").frame(maxWidth: .infinity) }
                .itedPrimaryButton().disabled(!isValid)
        }
        .navigationTitle("Separar pedido")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            drafts = order.itens.compactMap { line in
                guard let deposit = line.depositos.first(where: \.padrao) ?? line.depositos.first else { return nil }
                return SeparationDraft(
                    id: line.id, line: line, depositID: deposit.id,
                    quantity: Decimal.parse(line.quantidadeSolicitada), batchID: line.lotesSugeridos.first?.id
                )
            }
        }
    }

    private var isValid: Bool {
        drafts.count == order.itens.count && drafts.allSatisfy { $0.quantity > 0 && (!$0.line.controlaLote || $0.batchID != nil) }
    }

    private func complete() async {
        guard let session = model.session else { return }
        let submissions = drafts.compactMap { draft -> SeparationSubmissionItem? in
            guard let deposit = draft.line.depositos.first(where: { $0.id == draft.depositID }) else { return nil }
            let batch = draft.line.lotesSugeridos.first(where: { $0.id == draft.batchID })
            return SeparationSubmissionItem(line: draft.line, deposit: deposit, quantity: draft.quantity, batch: batch)
        }
        await model.perform {
            let number = try await model.api.completeSeparation(session: session, orderID: order.id, items: submissions, notes: notes)
            model.successMessage = "Separação #\(number) concluída."
            dismiss()
        }
    }
}
