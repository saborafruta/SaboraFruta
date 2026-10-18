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

struct NewOrderView: View {
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
        ScrollView {
            VStack(spacing: 16) {
                clientSection
                productSection
                orderItemsSection
                paymentSection
                deliverySection
                detailsSection
                submitSection
            }
            .padding()
        }
        .scrollDismissesKeyboard(.interactively)
        .background(Color(.systemGroupedBackground))
        .navigationTitle("Novo pedido")
        .navigationBarTitleDisplayMode(.inline)
        .task { await loadOptions() }
    }

    private var clientSection: some View {
        ITEDCard {
            Text("Cliente").font(.headline)
            if let client {
                HStack(spacing: 12) {
                    Image(systemName: "person.crop.circle.fill")
                        .font(.title2)
                        .foregroundStyle(ITEDStyle.blue)
                    VStack(alignment: .leading, spacing: 3) {
                        Text(client.nome).font(.subheadline.weight(.semibold)).lineLimit(2)
                        let detail = [client.cpfCnpj, client.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • ")
                        if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary) }
                    }
                    Spacer(minLength: 8)
                    Button("Trocar") {
                        self.client = nil
                        clientQuery = ""
                        clientResults = []
                        products = []
                    }
                    .buttonStyle(.bordered)
                    .buttonBorderShape(.capsule)
                }
                .padding(12)
                .background(Color(.secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 14))
                .padding(.top, 10)
            } else {
                TextField("Buscar por nome, CPF ou CNPJ", text: $clientQuery)
                    .textFieldStyle(.roundedBorder)
                    .submitLabel(.search)
                    .onChange(of: clientQuery) { _, value in searchClients(value) }
                    .padding(.top, 10)
                if !clientResults.isEmpty {
                    SearchResultPanel {
                        LazyVStack(spacing: 0) {
                            ForEach(clientResults.prefix(5)) { item in
                                Button {
                                    client = item
                                    clientQuery = ""
                                    clientResults = []
                                    products = []
                                } label: {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(item.nome).foregroundStyle(.primary)
                                        let detail = [item.cpfCnpj, item.cidadeUf].filter { !$0.isEmpty }.joined(separator: " • ")
                                        if !detail.isEmpty { Text(detail).font(.caption).foregroundStyle(.secondary) }
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .padding(12)
                                }
                                Divider()
                            }
                        }
                    }
                    .padding(.top, 8)
                }
            }
        }
    }

    private var productSection: some View {
        ITEDCard {
            HStack {
                Text("Adicionar produto").font(.headline)
                Spacer()
                Menu {
                    Button("Todas as categorias") { category = nil; Task { await searchProducts() } }
                    ForEach(options?.categories ?? []) { item in
                        Button(item.nome) { category = item; Task { await searchProducts() } }
                    }
                } label: {
                    Label(category?.nome ?? "Categoria", systemImage: "list.bullet")
                        .lineLimit(1)
                }
                .buttonStyle(.bordered)
            }
            HStack(spacing: 8) {
                TextField("Nome ou código", text: $productQuery)
                    .textFieldStyle(.roundedBorder)
                    .submitLabel(.search)
                    .onSubmit { Task { await searchProducts() } }
                Button { Task { await searchProducts() } } label: {
                    Image(systemName: "magnifyingglass")
                        .frame(minWidth: 28, minHeight: 28)
                }
                .buttonStyle(.borderedProminent)
                .tint(ITEDStyle.orange)
            }
            .padding(.top, 10)
            if !products.isEmpty {
                SearchResultPanel {
                    LazyVStack(spacing: 0) {
                        ForEach(products) { product in
                            Button { add(product) } label: {
                                HStack {
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(product.descricao).foregroundStyle(.primary).lineLimit(2)
                                        Text("\(product.categoria) • Estoque: \(product.estoqueDisponivel) \(product.unidade)")
                                            .font(.caption)
                                            .foregroundStyle(product.podeVender ? Color.secondary : Color.red)
                                    }
                                    Spacer(minLength: 8)
                                    Text(Decimal.parse(product.preco).currency)
                                        .fontWeight(.semibold)
                                        .foregroundStyle(ITEDStyle.orange)
                                }
                                .padding(12)
                            }
                            .disabled(!product.podeVender)
                            Divider()
                        }
                    }
                }
                .padding(.top, 8)
            }
        }
    }

    private var orderItemsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Itens do pedido").font(.headline)
                Spacer()
                Text("\(lines.count) produto(s)").font(.caption).foregroundStyle(.secondary)
            }
            if lines.isEmpty {
                ContentUnavailableView(
                    "Pedido vazio",
                    systemImage: "cart",
                    description: Text("Busque e adicione o primeiro produto.")
                )
                .frame(minHeight: 140)
                .background(.regularMaterial, in: RoundedRectangle(cornerRadius: ITEDStyle.radius))
            } else {
                ForEach(lines) { line in
                    ITEDCard {
                        HStack(alignment: .top, spacing: 12) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(line.product.descricao).font(.subheadline.bold()).lineLimit(2)
                                Text("\(Decimal.parse(line.product.preco).currency)/\(line.product.unidade)")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer(minLength: 8)
                            Button(role: .destructive) { removeProduct(line.id) } label: {
                                Image(systemName: "trash")
                                    .frame(width: 32, height: 32)
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Remover \(line.product.descricao) do pedido")
                        }
                        HStack(spacing: 10) {
                            Button { adjustQuantity(for: line.id, by: -1) } label: {
                                Image(systemName: "minus")
                                    .frame(width: 34, height: 34)
                            }
                            .buttonStyle(.bordered)
                            .accessibilityLabel("Diminuir quantidade de \(line.product.descricao)")

                            TextField(
                                "Quantidade",
                                value: quantityBinding(for: line.id),
                                format: .number.precision(.fractionLength(0...3))
                            )
                            .keyboardType(.decimalPad)
                            .multilineTextAlignment(.center)
                            .textFieldStyle(.roundedBorder)
                            .frame(minWidth: 76, maxWidth: 96)

                            Button { adjustQuantity(for: line.id, by: 1) } label: {
                                Image(systemName: "plus")
                                    .frame(width: 34, height: 34)
                            }
                            .buttonStyle(.bordered)
                            .accessibilityLabel("Aumentar quantidade de \(line.product.descricao)")

                            Spacer(minLength: 8)
                            Text(line.total.currency).fontWeight(.bold)
                        }
                        .padding(.top, 8)
                    }
                }
            }
        }
    }

    private var paymentSection: some View {
        ITEDCard {
            Text("Pagamento").font(.headline)
            Picker("Forma de pagamento", selection: $payment) {
                Text("Selecione").tag(nil as PaymentOption?)
                ForEach(options?.payments ?? []) { Text($0.descricao).tag(Optional($0)) }
            }
            .padding(.top, 6)
            Picker("Condição", selection: $condition) {
                Text("Sem condição").tag(nil as PaymentCondition?)
                ForEach(options?.conditions ?? []) { Text($0.descricao).tag(Optional($0)) }
            }
        }
    }

    private var deliverySection: some View {
        ITEDCard {
            Toggle("Informar entrega prevista", isOn: $hasDeliveryDate)
            if hasDeliveryDate {
                DatePicker("Data", selection: $deliveryDate, displayedComponents: .date)
                    .padding(.top, 4)
                HStack(spacing: 8) {
                    ForEach([5, 10, 15], id: \.self) { days in
                        Button("+\(days) dias") {
                            deliveryDate = Calendar.current.date(byAdding: .day, value: days, to: Date()) ?? Date()
                        }
                        .buttonStyle(.bordered)
                        .frame(maxWidth: .infinity)
                    }
                }
            }
        }
    }

    private var detailsSection: some View {
        ITEDCard {
            Text("Prioridade e observações").font(.headline)
            Picker("Prioridade", selection: $priority) {
                ForEach(options?.priorities ?? []) { Text($0.descricao).tag(Optional($0)) }
            }
            TextField("Observações", text: $notes, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(3...6)
        }
    }

    private var submitSection: some View {
        ITEDCard {
            HStack {
                Text("TOTAL DO PEDIDO").font(.caption.weight(.semibold))
                Spacer()
                Text(total.currency).font(.title2.bold())
            }
            Button { Task { await submit() } } label: {
                Label("Enviar pedido", systemImage: "paperplane.fill")
                    .frame(maxWidth: .infinity, minHeight: 28)
            }
            .itedPrimaryButton()
            .disabled(client == nil || payment == nil || priority == nil || lines.isEmpty)
            .padding(.top, 6)
        }
    }

    private func loadOptions() async {
        await model.perform {
            options = try await model.orderOptions()
            priority = options?.priorities.first
        }
    }

    private func searchClients(_ query: String) {
        searchTask?.cancel()
        guard query.count >= 2 else { clientResults = []; return }
        searchTask = Task {
            try? await Task.sleep(for: .milliseconds(350))
            guard !Task.isCancelled else { return }
            do { clientResults = try await model.clients(query: query) }
            catch { model.errorMessage = error.localizedDescription }
        }
    }

    private func searchProducts() async {
        guard let client else {
            model.errorMessage = "Selecione o cliente antes de buscar produtos."; return
        }
        await model.perform {
            products = try await model.orderProducts(
                client: client, query: productQuery,
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

    private func quantityBinding(for productID: Int) -> Binding<Decimal> {
        Binding(
            get: { lines.first { $0.id == productID }?.quantity ?? 0.001 },
            set: { updateQuantity(for: productID, to: $0) }
        )
    }

    private func adjustQuantity(for productID: Int, by amount: Decimal) {
        guard let current = lines.first(where: { $0.id == productID })?.quantity else { return }
        updateQuantity(for: productID, to: current + amount)
    }

    private func updateQuantity(for productID: Int, to quantity: Decimal) {
        guard let index = lines.firstIndex(where: { $0.id == productID }) else { return }
        lines[index].quantity = max(0.001, quantity)
    }

    private func removeProduct(_ productID: Int) {
        lines.removeAll { $0.id == productID }
    }

    private func submit() async {
        guard let client, let payment, let priority else { return }
        await model.perform {
            let result = try await model.submitOrder(
                client: client, lines: lines, payment: payment, condition: condition,
                priority: priority, deliveryDate: hasDeliveryDate ? deliveryDate : nil, notes: notes
            )
            switch result {
            case let .sent(order): model.successMessage = "Pedido #\(order.numeroPedido) enviado ao iTED."
            case .queued: model.successMessage = "Pedido salvo offline. Ele será enviado na próxima sincronização."
            }
            dismiss()
        }
    }
}
