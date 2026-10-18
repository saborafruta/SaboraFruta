import Foundation

struct AuthTokens: Codable, Sendable {
    let access: String
    let refresh: String
}

struct WorkContext: Codable, Identifiable, Hashable, Sendable {
    let tenantAlias: String
    let empresaId: Int
    let empresaNome: String
    let filialId: Int
    let filialNome: String
    let filialCnpj: String
    let podeCriarPedido: Bool
    let podeSeparar: Bool
    let podeCheckout: Bool

    var id: String { "\(tenantAlias)-\(filialId)" }
}

struct UserSession: Codable, Sendable {
    var tokens: AuthTokens
    let usuarioNome: String
    let usuarioEmail: String
    var context: WorkContext
}

struct Client: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let nome: String
    let cpfCnpj: String
    let cidade: String
    let uf: String
    let creditoDisponivel: String
    let tabelaPreco: String
    let prazoPagamentoDias: Int
    let bloqueado: Bool
    let motivoBloqueio: String

    var cidadeUf: String { [cidade, uf].filter { !$0.isEmpty }.joined(separator: "/") }
}

struct StockBalance: Codable, Identifiable, Hashable, Sendable {
    let depositoId: Int
    let depositoNome: String
    let disponivel: String
    let permiteVenda: Bool
    let origemPedido: Bool
    var id: Int { depositoId }
}

struct Product: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let descricao: String
    let codigo: String
    let unidade: String
    let preco: String
    let precoOrigem: String
    let categoria: String
    let destaque: Bool
    let estoqueDisponivel: String
    let controlaLote: Bool
    let podeVender: Bool
    let bloqueios: [String]
    let estoques: [StockBalance]
}

struct PaymentOption: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let descricao: String
    let tipo: String
}

struct CheckoutState: Sendable {
    let sessionOpen: Bool
    let cashRegisterName: String?
    let payments: [PaymentOption]
    let canApplyDiscount: Bool
    let discountPercentages: [String]
    let maxDiscountPercent: Decimal
    let maxRoundingDiscount: Decimal
}

struct CheckoutLine: Identifiable, Hashable, Sendable {
    let product: Product
    var quantity: Decimal
    var id: Int { product.id }
    var total: Decimal { (Decimal(string: product.preco) ?? 0) * quantity }
}

struct CheckoutPendingSummary: Codable, Identifiable, Sendable {
    let id: Int
    let numeroVenda: String
    let clienteNome: String
    let valorTotal: String
    let desconto: String
    let dataVenda: String
    let quantidadeItens: Int
}

struct CheckoutPendingDetail: Codable, Sendable {
    let id: Int
    let numeroVenda: String
    let clienteNome: String
    let cliente: Client?
    let valorTotal: String
    let desconto: String
    let dataVenda: String
    let quantidadeItens: Int
    let formaPagamentoId: Int?
    let itens: [PendingCheckoutLine]
}

struct PendingCheckoutLine: Codable, Sendable {
    let quantidade: String
    let produto: Product
}

struct OrderSummary: Codable, Identifiable, Sendable {
    let id: Int
    let numeroPedido: String
    let clienteNome: String
    let status: String
    let statusDescricao: String
    let valorTotal: String
    let dataEmissao: String
}

struct OrderOptions: Sendable {
    let payments: [PaymentOption]
    let conditions: [PaymentCondition]
    let categories: [ProductCategory]
    let priorities: [OrderPriority]
}

struct PaymentCondition: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let descricao: String
    let numeroParcelas: Int
    let intervaloDias: Int
    let diasPrimeiraParcela: Int
}

struct ProductCategory: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let nome: String
}

struct OrderPriority: Codable, Identifiable, Hashable, Sendable {
    let codigo: String
    let descricao: String
    var id: String { codigo }
}

struct SeparationDeposit: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let nome: String
    let disponivel: String
    let padrao: Bool
}

struct BatchSuggestion: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let numero: String
    let quantidade: String
    let validade: String?
}

struct SeparationLine: Codable, Identifiable, Hashable, Sendable {
    let id: Int
    let numeroItem: Int
    let produtoDescricao: String
    let produtoCodigo: String
    let unidade: String
    let quantidadeSolicitada: String
    let controlaLote: Bool
    let lotesSugeridos: [BatchSuggestion]
    let depositos: [SeparationDeposit]
}

struct SeparationOrder: Codable, Identifiable, Sendable {
    let id: Int
    let numeroPedido: String
    let clienteNome: String
    let prioridade: String
    let dataEntregaPrevista: String?
    let status: String
    let itens: [SeparationLine]
}

struct SeparationSubmissionItem: Sendable {
    let line: SeparationLine
    var deposit: SeparationDeposit
    var quantity: Decimal
    var batch: BatchSuggestion?
}

struct CepAddress: Codable, Sendable {
    let cep: String
    let endereco: String
    let complemento: String
    let bairro: String
    let cidade: String
    let uf: String
    let codigoMunicipioIbge: String
}

struct CompanyLookup: Codable, Sendable {
    let cnpj: String
    let razaoSocial: String
    let nomeFantasia: String
    let inscricaoEstadual: String
    let endereco: String
    let numero: String
    let complemento: String
    let bairro: String
    let cidade: String
    let uf: String
    let cep: String
    let codigoMunicipioIbge: String
    let telefone: String
    let email: String
    let situacaoCadastral: String
}

struct ClientRegistration: Encodable, Sendable {
    var tipoPessoa = "F"
    var tipo = "varejo"
    var razaoSocial = ""
    var nomeFantasia = ""
    var cpfCnpj = ""
    var rgIe = ""
    var inscricaoMunicipal = ""
    var inscricaoEstadual = ""
    var dataNascimento: String?
    var sexo = ""
    var endereco = ""
    var numero = ""
    var complemento = ""
    var pontoReferencia = ""
    var bairro = ""
    var cidade = ""
    var uf = ""
    var cep = ""
    var codigoMunicipioIbge = ""
    var telefone = ""
    var celular = ""
    var email = ""
    var emailNfe = ""
    var contatoNome = ""
    var limiteCredito = "0"
    var prazoPagamentoDias = 0
    var consumidorFinal = true
    var contribuinteIcms = false
    var optanteSimples = false
    var bloqueado = false
    var motivoBloqueio = ""
    var observacao = ""
}

struct OrderDraftLine: Identifiable, Hashable, Sendable {
    let product: Product
    var quantity: Decimal
    var id: Int { product.id }
    var total: Decimal { .parse(product.preco) * quantity }
}

struct APIMessage: Codable, Sendable {
    let detail: String?
}

extension Decimal {
    static func parse(_ value: String) -> Decimal {
        Decimal(string: value.replacingOccurrences(of: ",", with: ".")) ?? 0
    }

    var apiString: String { NSDecimalNumber(decimal: self).stringValue }
    var currency: String {
        self.formatted(.currency(code: "BRL").locale(Locale(identifier: "pt_BR")))
    }

    func rounded(_ scale: Int) -> Decimal {
        var source = self
        var result = Decimal()
        NSDecimalRound(&result, &source, scale, .plain)
        return result
    }
}
