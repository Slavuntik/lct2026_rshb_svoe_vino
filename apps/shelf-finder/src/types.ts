export type Box = [number, number, number, number];
export interface Detection { box: Box; score: number }
export interface Wine { id: string; name: string; brand: string; region: string; group: string; references: number[][] }
export interface Catalog { version: 1; embeddingModel: string; dimension: number; wines: Wine[] }
export interface Match { id: string | null; score: number; margin: number; candidates: { id: string; score: number }[] }
export interface Observation extends Detection { match: Match; embedding: number[]; tooSmall: boolean; verification?: { id: string; inliers: number; matches: number; coverage: number }; geometry?: { id: string; inliers: number; matches: number; coverage: number }[] }
export interface Track extends Observation { trackId: number; seen: number; confirmed: boolean; lastSeen: number }
export interface ModelManifest { version: 1; detector: string; embedder: string; catalog: string; embeddingModel: string; dimension: number; detectorSize: number; bottleClass: number; hashes: Record<string, string>; preprocessing?: 'siglip-full-label' | 'mobilenet-label'; embeddingOutput?: string; threshold?: number; margin?: number; geometryReferences?: string; localFeatures?: string; localExtractor?: 'aliked' | 'xfeat'; matcher?: string; retriever?: string; descriptorDot?: string; matcherPoints?: number; matcherLayers?: number }
export interface ScanResult { type: 'result'; requestId: number; observations: Observation[]; elapsed: number; detectionMs: number; recognitionMs: number; recognitionBackend?: string; detectedCount?: number; serverProcessingMs?: number; threads?: number }
