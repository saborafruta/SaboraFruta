// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "ITEDForcaVendas",
    platforms: [.iOS(.v17)],
    products: [
        .library(name: "ITEDForcaVendas", targets: ["ITEDForcaVendas"]),
    ],
    targets: [
        .target(
            name: "ITEDForcaVendas",
            resources: [.process("Resources")]
        ),
        .testTarget(
            name: "ITEDForcaVendasTests",
            dependencies: ["ITEDForcaVendas"]
        ),
    ],
    swiftLanguageModes: [.v6]
)
