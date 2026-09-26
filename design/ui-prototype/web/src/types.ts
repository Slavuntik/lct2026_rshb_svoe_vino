export type WineColor = "red" | "white" | "rose" | "orange" | "sparkling";

export interface WinePairing {
  label: string;
  imageUrl: string;
}

export interface Wine {
  slug: string;
  name: string;
  producer: string;
  region: string;
  grape: string;
  year: number;
  color: WineColor;
  description: string;
  roskachestvoRating: number | null;
  pairing: string;
  alcohol: number;
  volume: number;
  price: number | null;
  imageUrl: string;
  servingTemp?: string;
  category?: string;
  vineyardImageUrl?: string;
  pairings?: WinePairing[];
}

export interface ScanResult {
  found: boolean;
  wine: Wine | null;
  confidence: { f1Top1: number; f1Top5: number };
  candidates: Array<{ slug: string; score: number }>;
  similar: Wine[];
}

export interface SommelierRequest {
  occasion: string;
  dish: string;
  sweetness: string;
  budget: string;
}

export interface SommelierResponse {
  wines: Wine[];
  rationale: string;
}
