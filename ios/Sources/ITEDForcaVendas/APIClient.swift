import Foundation

enum APIError: LocalizedError {
    case invalidResponse
    case http(Int, String)
    case offline
    case decoding

    var errorDescription: String? {
        switch self {
        case .invalidResponse: "Resposta inválida do iTED."
        case let .http(_, message): message
        case .offline: "Sem conexão com o iTED. Confira sua internet e tente novamente."
        case .decoding: "O iTED respondeu em um formato inesperado."
        }
    }
}

struct APIClient: Sendable {
    let baseURL: URL
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(baseURL: URL = URL(string: "https://ited.app.br/")!) {
        self.baseURL = baseURL
        encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
    }

    func login(email: String, password: String) async throws -> AuthTokens {
        struct Body: Encodable { let email: String; let password: String }
        return try await request("api/v1/mobile/token/", method: "POST", body: Body(email: email, password: password))
    }

    func contexts(accessToken: String) async throws -> (String, String, [WorkContext]) {
        struct Permissions: Decodable {
            let pedidosCriar: Bool
            let separacaoEditar: Bool
            let checkoutVender: Bool
        }
        struct ContextDTO: Decodable {
            let tenantAlias: String
            let empresaId: Int
            let empresaNome: String
            let filialId: Int
            let filialNome: String
            let filialCnpj: String
            let permissoes: Permissions
        }
        struct UserDTO: Decodable { let nome: String; let email: String }
        struct Response: Decodable { let usuario: UserDTO; let contextos: [ContextDTO] }
        let response: Response = try await request("api/v1/mobile/contexto/", token: accessToken)
        let contexts = response.contextos.map {
            WorkContext(
                tenantAlias: $0.tenantAlias,
                empresaId: $0.empresaId,
                empresaNome: $0.empresaNome,
                filialId: $0.filialId,
                filialNome: $0.filialNome,
                filialCnpj: $0.filialCnpj,
                podeCriarPedido: $0.permissoes.pedidosCriar,
                podeSeparar: $0.permissoes.separacaoEditar,
                podeCheckout: $0.permissoes.checkoutVender
            )
        }
        return (response.usuario.nome, response.usuario.email, contexts)
    }

    func clients(session: UserSession, query: String = "") async throws -> [Client] {
        struct Response: Decodable { let clientes: [Client] }
        let path = "api/v1/mobile/clientes/?q=\(query.urlEncoded)"
        let response: Response = try await authorized(path, session: session)
        return response.clientes
    }

    func createClient(session: UserSession, registration: ClientRegistration) async throws -> Client {
        struct Response: Decodable { let cliente: Client }
        let response: Response = try await authorized(
            "api/v1/mobile/clientes/", method: "POST", session: session, body: registration
        )
        return response.cliente
    }

    func lookupCep(session: UserSession, cep: String) async throws -> CepAddress {
        struct Response: Decodable { let endereco: CepAddress }
        let response: Response = try await authorized(
            "api/v1/mobile/clientes/cep/?cep=\(cep.urlEncoded)", session: session
        )
        return response.endereco
    }

    func lookupCompany(session: UserSession, cnpj: String) async throws -> CompanyLookup {
        struct Response: Decodable { let empresa: CompanyLookup }
        let response: Response = try await authorized(
            "api/v1/mobile/clientes/cnpj/?cnpj=\(cnpj.urlEncoded)", session: session
        )
        return response.empresa
    }

    func checkoutState(session: UserSession) async throws -> CheckoutState {
        struct Cash: Decodable { let caixaNome: String? }
        struct Discount: Decodable {
            let percentuais: [String]
            let percentualMaximo: String
            let arredondamentoMaximo: String
        }
        struct Response: Decodable {
            let sessao: Cash?
            let formasPagamento: [PaymentOption]
            let podeAplicarDesconto: Bool
            let descontos: Discount
        }
        let response: Response = try await authorized("api/v1/mobile/checkout/estado/", session: session)
        return CheckoutState(
            sessionOpen: response.sessao != nil,
            cashRegisterName: response.sessao?.caixaNome,
            payments: response.formasPagamento,
            canApplyDiscount: response.podeAplicarDesconto,
            discountPercentages: response.descontos.percentuais,
            maxDiscountPercent: .parse(response.descontos.percentualMaximo),
            maxRoundingDiscount: .parse(response.descontos.arredondamentoMaximo)
        )
    }

    func checkoutClients(session: UserSession, query: String) async throws -> [Client] {
        struct Response: Decodable { let clientes: [Client] }
        let response: Response = try await authorized(
            "api/v1/mobile/checkout/clientes/?q=\(query.urlEncoded)", session: session
        )
        return response.clientes
    }

    func createCheckoutClient(
        session: UserSession,
        name: String,
        document: String,
        phone: String
    ) async throws -> Client {
        struct Body: Encodable {
            let nome: String
            let cpfCnpj: String
            let celular: String
        }
        struct Response: Decodable { let cliente: Client }
        let response: Response = try await authorized(
            "api/v1/mobile/checkout/clientes/", method: "POST", session: session,
            body: Body(
                nome: name.trimmingCharacters(in: .whitespacesAndNewlines),
                cpfCnpj: document.trimmingCharacters(in: .whitespacesAndNewlines),
                celular: phone.trimmingCharacters(in: .whitespacesAndNewlines)
            )
        )
        return response.cliente
    }

    func checkoutProducts(session: UserSession, query: String, clientID: Int?) async throws -> [Product] {
        struct Response: Decodable { let produtos: [Product] }
        var path = "api/v1/mobile/checkout/produtos/?q=\(query.urlEncoded)"
        if let clientID { path += "&cliente_id=\(clientID)" }
        let response: Response = try await authorized(path, session: session)
        return response.produtos
    }

    func authorizeCheckoutDiscount(session: UserSession, email: String, password: String) async throws -> String {
        struct Body: Encodable { let email: String; let senha: String }
        struct Response: Decodable { let token: String; let autorizadoPor: String }
        let response: Response = try await authorized(
            "api/v1/mobile/checkout/descontos/autorizar/", method: "POST", session: session,
            body: Body(email: email, senha: password)
        )
        return response.token
    }

    func finalizeCheckout(
        session: UserSession,
        client: Client?,
        lines: [CheckoutLine],
        payment: PaymentOption,
        discount: Decimal,
        received: Decimal,
        pendingID: Int? = nil,
        discountAuthorization: String? = nil
    ) async throws -> String {
        struct Item: Encodable { let produtoId: Int; let quantidade: String }
        struct Payment: Encodable { let formaId: Int; let valor: String }
        struct Body: Encodable {
            let pendenciaId: Int?
            let clienteId: Int?
            let desconto: String
            let autorizacaoDesconto: String
            let itens: [Item]
            let pagamentos: [Payment]
        }
        struct Response: Decodable { let numeroVenda: Int }
        let body = Body(
            pendenciaId: pendingID,
            clienteId: client?.id,
            desconto: discount.apiString,
            autorizacaoDesconto: discountAuthorization ?? "",
            itens: lines.map { Item(produtoId: $0.product.id, quantidade: $0.quantity.apiString) },
            pagamentos: [Payment(formaId: payment.id, valor: received.apiString)]
        )
        let response: Response = try await authorized(
            "api/v1/mobile/checkout/vendas/", method: "POST", session: session,
            body: body, idempotencyKey: UUID().uuidString
        )
        return String(response.numeroVenda)
    }

    func saveCheckoutPending(
        session: UserSession,
        client: Client?,
        lines: [CheckoutLine],
        payment: PaymentOption?,
        discount: Decimal,
        received: Decimal,
        discountAuthorization: String? = nil
    ) async throws -> CheckoutPendingSummary {
        struct Item: Encodable { let produtoId: Int; let quantidade: String }
        struct Payment: Encodable { let formaId: Int; let valor: String }
        struct Body: Encodable {
            let clienteId: Int?
            let desconto: String
            let autorizacaoDesconto: String
            let itens: [Item]
            let pagamentos: [Payment]
        }
        let body = Body(
            clienteId: client?.id,
            desconto: discount.apiString,
            autorizacaoDesconto: discountAuthorization ?? "",
            itens: lines.map { Item(produtoId: $0.product.id, quantidade: $0.quantity.apiString) },
            pagamentos: payment.map { [Payment(formaId: $0.id, valor: received.apiString)] } ?? []
        )
        return try await authorized(
            "api/v1/mobile/checkout/pendencias/", method: "POST", session: session,
            body: body, idempotencyKey: UUID().uuidString
        )
    }

    func checkoutPendings(session: UserSession) async throws -> [CheckoutPendingSummary] {
        struct Response: Decodable { let pendencias: [CheckoutPendingSummary] }
        let response: Response = try await authorized("api/v1/mobile/checkout/pendencias/", session: session)
        return response.pendencias
    }

    func checkoutPending(session: UserSession, id: Int) async throws -> CheckoutPendingDetail {
        try await authorized("api/v1/mobile/checkout/pendencias/\(id)/", session: session)
    }

    func orders(session: UserSession) async throws -> [OrderSummary] {
        struct Response: Decodable { let pedidos: [OrderSummary] }
        let response: Response = try await authorized("api/v1/mobile/pedidos/", session: session)
        return response.pedidos
    }


    func orderOptions(session: UserSession) async throws -> OrderOptions {
        struct Response: Decodable {
            let formasPagamento: [PaymentOption]
            let condicoesPagamento: [PaymentCondition]
            let categorias: [ProductCategory]
            let prioridades: [OrderPriority]
        }
        let response: Response = try await authorized("api/v1/mobile/opcoes-pedido/", session: session)
        return OrderOptions(
            payments: response.formasPagamento,
            conditions: response.condicoesPagamento,
            categories: response.categorias,
            priorities: response.prioridades
        )
    }

    func orderProducts(session: UserSession, clientID: Int, query: String = "", categoryID: Int? = nil, featured: Bool = false) async throws -> [Product] {
        struct Response: Decodable { let produtos: [Product] }
        var path = "api/v1/mobile/produtos/?cliente_id=\(clientID)&q=\(query.urlEncoded)"
        if let categoryID { path += "&categoria_id=\(categoryID)" }
        if featured { path += "&destaques=1" }
        let response: Response = try await authorized(path, session: session)
        return response.produtos
    }

    func createOrder(
        session: UserSession,
        client: Client,
        lines: [OrderDraftLine],
        payment: PaymentOption,
        condition: PaymentCondition?,
        priority: OrderPriority,
        deliveryDate: Date?,
        notes: String
    ) async throws -> OrderSummary {
        struct Item: Encodable { let produtoId: Int; let quantidade: String }
        struct Body: Encodable {
            let clienteId: Int
            let formaPagamentoId: Int
            let condicaoPagamentoId: Int?
            let prazoPagamentoDias: Int
            let prioridade: String
            let dataEntregaPrevista: String?
            let observacao: String
            let itens: [Item]
        }
        struct Response: Decodable { let pedido: OrderSummary }
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        let body = Body(
            clienteId: client.id,
            formaPagamentoId: payment.id,
            condicaoPagamentoId: condition?.id,
            prazoPagamentoDias: condition?.diasPrimeiraParcela ?? client.prazoPagamentoDias,
            prioridade: priority.codigo,
            dataEntregaPrevista: deliveryDate.map(formatter.string),
            observacao: notes,
            itens: lines.map { Item(produtoId: $0.product.id, quantidade: $0.quantity.apiString) }
        )
        let response: Response = try await authorized(
            "api/v1/mobile/pedidos/", method: "POST", session: session,
            body: body, idempotencyKey: UUID().uuidString
        )
        return response.pedido
    }

    func separations(session: UserSession) async throws -> [SeparationOrder] {
        struct Response: Decodable { let pedidos: [SeparationOrder] }
        let response: Response = try await authorized("api/v1/mobile/separacoes/", session: session)
        return response.pedidos
    }

    func completeSeparation(session: UserSession, orderID: Int, items: [SeparationSubmissionItem], notes: String) async throws -> String {
        struct Lot: Encodable { let loteId: Int; let quantidade: String }
        struct Item: Encodable {
            let itemId: Int
            let depositoId: Int
            let quantidadeSeparada: String
            let conferido: Bool
            let observacao: String
            let lotes: [Lot]
        }
        struct Body: Encodable { let observacao: String; let itens: [Item] }
        struct Response: Decodable { let numero: String }
        let body = Body(
            observacao: notes,
            itens: items.map { item in
                Item(
                    itemId: item.line.id,
                    depositoId: item.deposit.id,
                    quantidadeSeparada: item.quantity.apiString,
                    conferido: true,
                    observacao: "",
                    lotes: item.batch.map { [Lot(loteId: $0.id, quantidade: item.quantity.apiString)] } ?? []
                )
            }
        )
        let response: Response = try await authorized(
            "api/v1/mobile/separacoes/\(orderID)/concluir/", method: "POST", session: session,
            body: body, idempotencyKey: UUID().uuidString
        )
        return response.numero
    }

    private func authorized<Response: Decodable>(
        _ path: String,
        method: String = "GET",
        session: UserSession,
        idempotencyKey: String? = nil
    ) async throws -> Response {
        try await request(
            path, method: method, token: session.tokens.access,
            context: session.context, idempotencyKey: idempotencyKey
        )
    }

    private func authorized<Body: Encodable, Response: Decodable>(
        _ path: String,
        method: String,
        session: UserSession,
        body: Body,
        idempotencyKey: String? = nil
    ) async throws -> Response {
        try await request(
            path, method: method, body: body, token: session.tokens.access,
            context: session.context, idempotencyKey: idempotencyKey
        )
    }

    private func request<Response: Decodable>(
        _ path: String,
        method: String = "GET",
        token: String? = nil,
        context: WorkContext? = nil,
        idempotencyKey: String? = nil
    ) async throws -> Response {
        try await perform(path, method: method, body: nil, token: token, context: context, idempotencyKey: idempotencyKey)
    }

    private func request<Body: Encodable, Response: Decodable>(
        _ path: String,
        method: String,
        body: Body,
        token: String? = nil,
        context: WorkContext? = nil,
        idempotencyKey: String? = nil
    ) async throws -> Response {
        try await perform(
            path, method: method, body: try encoder.encode(body), token: token,
            context: context, idempotencyKey: idempotencyKey
        )
    }

    private func perform<Response: Decodable>(
        _ path: String,
        method: String,
        body: Data?,
        token: String?,
        context: WorkContext?,
        idempotencyKey: String?
    ) async throws -> Response {
        guard let url = URL(string: path, relativeTo: baseURL) else { throw APIError.invalidResponse }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let context {
            request.setValue(String(context.filialId), forHTTPHeaderField: "X-Filial-ID")
            request.setValue(context.tenantAlias, forHTTPHeaderField: "X-Tenant-Alias")
        }
        if let idempotencyKey { request.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key") }

        let data: Data
        let response: URLResponse
        do { (data, response) = try await URLSession.shared.data(for: request) }
        catch { throw APIError.offline }
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard 200..<300 ~= http.statusCode else {
            throw APIError.http(http.statusCode, Self.errorMessage(from: data))
        }
        do { return try decoder.decode(Response.self, from: data) }
        catch { throw APIError.decoding }
    }

    private static func errorMessage(from data: Data) -> String {
        guard let object = try? JSONSerialization.jsonObject(with: data) else {
            return "Não foi possível concluir a operação."
        }
        func firstMessage(_ value: Any) -> String? {
            if let text = value as? String { return text }
            if let values = value as? [Any] { return values.compactMap(firstMessage).first }
            if let values = value as? [String: Any] { return values.values.compactMap(firstMessage).first }
            return nil
        }
        return firstMessage(object) ?? "Não foi possível concluir a operação."
    }
}

private extension String {
    var urlEncoded: String { addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? self }
}
