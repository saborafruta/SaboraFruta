import SwiftUI

struct OrdersView: View {
    @Environment(AppModel.self) private var model
    @State private var orders: [OrderSummary] = []

    var body: some View {
        List(orders) { order in
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text("Pedido #\(order.numeroPedido)").font(.headline)
                    Spacer()
                    Text(order.statusDescricao).font(.caption.bold()).foregroundStyle(ITEDStyle.blue)
                }
                Text(order.clienteNome)
                HStack { Text(order.dataEmissao).font(.caption).foregroundStyle(.secondary); Spacer(); Text(Decimal.parse(order.valorTotal).currency).bold() }
            }.padding(.vertical, 5)
        }
        .overlay { if orders.isEmpty && !model.isBusy { ContentUnavailableView("Nenhum pedido", systemImage: "doc.text") } }
        .navigationTitle("Pedidos")
        .toolbar { ToolbarItem(placement: .topBarTrailing) { NavigationLink { NewOrderView() } label: { Image(systemName: "plus") } } }
        .refreshable { await load() }
        .task { await load() }
    }

    private func load() async {
        guard let session = model.session else { return }
        await model.perform { orders = try await model.api.orders(session: session); model.lastSync = Date() }
    }
}

private struct NewOrderView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var options: OrderOptions?
    @State private var client: Client?
    @State private var clientQuery = ""
    @State private var clientResults: [Client] = []
    @State private var category: ProductCategory?
    @State private var productQuery = ""
    @State private var products: [Product] = []
    @State private var lines: [OrderDraftLine] = []
    @State private var payment: PaymentOption?
    @State private var condition: PaymentCondition?
    @State private var priority: OrderPriority?
    @State private var hasDeliveryDate = true
    @State private var deliveryDate = Calendar.current.date(byAdding: .day, value: 5, to: Date()) ?? Date()
    @State private var notes = ""
    @State private var searchTask: Task<Void, Never>?

    private var total: Decimal { lines.reduce(0) { $0 + $1.total } }

    var body: some View {
        Form {
            Section("Cliente") {
                TextField("Buscar cliente", text: $clientQuery)
                    .onChange(of: clientQuery) { _, value in searchClients(value) }
                if !clientResults.isEmpty {
                    ForEach(clientResults.prefix(5)) { item in
                        Button(item.nome) { client = item; clientQuery = item.nome; clientResults = []; products = [] }
                    }
                }
                if let client { LabeledContent("Selecionado", value: client.nome) }
            }

            Section("Produtos") {
                Menu {
                    Button("Todas as categorias") { category = nil; Task { await searchProducts() } }
                    ForEach(options?.categories ?? []) { item in Button(item.nome) { category = item; Task { await searchProducts() } } }
                } label: { Label(category?.nome ?? "Selecionar categoria", systemImage: "list.bullet") }
                HStack {
                    TextField("Nome ou código", text: $productQuery)
                    Button { Task { await searchProducts() } } label: { Image(systemName: "magnifyingglass") }
                }
                if !products.isEmpty {
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            ForEach(products) { product in
                                Button { add(product) } label: {
                                    HStack {
                                        VStack(alignment: .leading) {
                                            Text(product.descricao).foregroundStyle(.primary)
                                            Text("\(product.categoria) • Estoque \(product.estoqueDisponivel)").font(.caption).foregroundStyle(.secondary)
                                        }
                                        Spacer(); Text(Decimal.parse(product.preco).currency)
                                    }.padding(.vertical, 10)
                                }.disabled(!product.podeVender)
                                Divider()
                            }
                        }
                    }.frame(maxHeight: 260)
                }
            }

            if !lines.isEmpty {
                Section("Itens do pedido") {
                    ForEach($lines) { $line in
                        VStack(alignment: .leading) {
                            Text(line.product.descricao).font(.subheadline.bold())
                            HStack {
                                Button { line.quantity = max(0.001, line.quantity - 1) } label: { Image(systemName: "minus.circle") }
                                TextField("Qtd.", value: $line.quantity, format: .number.precision(.fractionLength(0...3))).keyboardType(.decimalPad).frame(width: 74)
                                Button { line.quantity += 1 } label: { Image(systemName: "plus.circle") }
                                Spacer(); Text(line.total.currency)
                                Button(role: .destructive) { lines.removeAll { $0.id == line.id } } label: { Image(systemName: "trash") }
                            }
                        }
                    }
                }
            }

            Section("Pagamento") {
                Picker("Forma de pagamento", selection: $payment) {
                    Text("Selecione").tag(nil as PaymentOption?)
                    ForEach(options?.payments ?? []) { Text($0.descricao).tag(Optional($0)) }
                }
                Picker("Condição", selection: $condition) {
                    Text("Sem condição").tag(nil as PaymentCondition?)
                    ForEach(options?.conditions ?? []) { Text($0.descricao).tag(Optional($0)) }
                }
            }

            Section("Entrega prevista") {
                Toggle("Informar data", isOn: $hasDeliveryDate)
                if hasDeliveryDate {
                    DatePicker("Data", selection: $deliveryDate, displayedComponents: .date)
                    HStack {
                        ForEach([5, 10, 15], id: \.self) { days in
                            Button("+\(days) dias") { deliveryDate = Calendar.current.date(byAdding: .day, value: days, to: Date()) ?? Date() }
                        }
                    }
                }
            }

            Section("Prioridade e observações") {
                Picker("Prioridade", selection: $priority) {
                    ForEach(options?.priorities ?? []) { Text($0.descricao).tag(Optional($0)) }
                }
                TextField("Observações", text: $notes, axis: .vertical).lineLimit(3...6)
            }

            Section {
                HStack { Text("Total"); Spacer(); Text(total.currency).font(.title3.bold()) }
                Button { Task { await submit() } } label: { Text("Enviar pedido").frame(maxWidth: .infinity) }
                    .itedPrimaryButton().disabled(client == nil || payment == nil || priority == nil || lines.isEmpty)
            }
        }
        .navigationTitle("Novo pedido")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadOptions() }
    }

    private func loadOptions() async {
        guard let session = model.session else { return }
        await model.perform {
            options = try await model.api.orderOptions(session: session)
            priority = options?.priorities.first
        }
    }

    private func searchClients(_ query: String) {
        searchTask?.cancel()
        guard query.count >= 2, let session = model.session else { clientResults = []; return }
        searchTask = Task {
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled else { return }
            do { clientResults = try await model.api.clients(session: session, query: query) }
            catch { model.errorMessage = error.localizedDescription }
        }
    }

    private func searchProducts() async {
        guard let session = model.session, let client else {
            model.errorMessage = "Selecione o cliente antes de buscar produtos."; return
        }
        await model.perform {
            products = try await model.api.orderProducts(
                session: session, clientID: client.id, query: productQuery,
                categoryID: category?.id, featured: productQuery.isEmpty && category == nil
            )
        }
    }

    private func add(_ product: Product) {
        guard product.podeVender else {
            model.errorMessage = "\(product.descricao): \(product.bloqueios.joined(separator: ", "))"; return
        }
        if let index = lines.firstIndex(where: { $0.id == product.id }) { lines[index].quantity += 1 }
        else { lines.append(OrderDraftLine(product: product, quantity: 1)) }
    }

    private func submit() async {
        guard let session = model.session, let client, let payment, let priority else { return }
        await model.perform {
            let order = try await model.api.createOrder(
                session: session, client: client, lines: lines, payment: payment, condition: condition,
                priority: priority, deliveryDate: hasDeliveryDate ? deliveryDate : nil, notes: notes
            )
            model.successMessage = "Pedido #\(order.numeroPedido) enviado ao iTED."
            dismiss()
        }
    }
}
