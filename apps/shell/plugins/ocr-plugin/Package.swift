// swift-tools-version: 5.9
import PackageDescription

// Имя пакета/продукта обязано совпадать с тем, что сгенерирует @capacitor/cli из имени npm-
// пакета (fixName("svoy-somelye-ocr-plugin") -> "SvoySomelyeOcrPlugin"), иначе `cap sync ios`
// пропишет в CapApp-SPM/Package.swift ссылку на продукт, которого здесь нет.
let package = Package(
    name: "SvoySomelyeOcrPlugin",
    platforms: [.iOS(.v15)],
    products: [
        .library(
            name: "SvoySomelyeOcrPlugin",
            targets: ["SvoySomelyeOcrPlugin"]
        )
    ],
    dependencies: [
        .package(url: "https://github.com/ionic-team/capacitor-swift-pm.git", from: "8.0.0")
    ],
    targets: [
        .target(
            name: "SvoySomelyeOcrPlugin",
            dependencies: [
                .product(name: "Capacitor", package: "capacitor-swift-pm")
            ],
            path: "Sources/SvoySomelyeOcrPlugin"
        )
    ]
)
