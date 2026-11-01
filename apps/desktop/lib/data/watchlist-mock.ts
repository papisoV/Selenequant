export interface WatchlistItem {
  sym: string;
  exch: string;
  price: string;
  chg: number;
  signal: "up" | "down" | "signal";
  sparkline: number[];
}

export interface WatchlistGroup {
  name: string;
  items: WatchlistItem[];
}
