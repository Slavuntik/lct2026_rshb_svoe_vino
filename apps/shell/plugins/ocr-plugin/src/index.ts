import { registerPlugin } from "@capacitor/core";
import type { OcrPlugin } from "./definitions";

// jsName в Swift-стороне (OcrPlugin.swift: `public let jsName = "Ocr"`) обязан совпадать
// с этой строкой — это то, как нативный мост находит класс плагина по имени.
const Ocr = registerPlugin<OcrPlugin>("Ocr", {
  web: () => import("./web").then((module) => new module.OcrWeb()),
});

export * from "./definitions";
export { Ocr };
