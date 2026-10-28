import type { ApiGraph } from "../types";

export type Recipe = {
  id: string;
  title: string;
  blurb: string;
  graph: ApiGraph;
};

function chain(
  name: string,
  steps: Array<{ id: string; type: string; data: Record<string, unknown>; handle?: string }>,
  links: Array<{ from: string; to: string; sourceHandle?: string; targetHandle?: string }>,
): ApiGraph {
  return {
    name,
    nodes: steps.map((step, index) => ({
      id: step.id,
      type: step.type,
      position: { x: 40 + index * 300, y: 120 },
      data: step.data,
    })),
    edges: links.map((link, index) => ({
      id: `e${index + 1}`,
      source: link.from,
      target: link.to,
      sourceHandle: link.sourceHandle ?? "out",
      targetHandle: link.targetHandle ?? "in",
    })),
  };
}

export const RECIPES: Recipe[] = [
  {
    id: "top-10-holders",
    title: "Top 10 holders",
    blurb: "Largest balances at the end of the window. Change 10 to 50 in First rows.",
    graph: chain(
      "Top 10 holders",
      [
        { id: "holders1", type: "source", data: { source: "holders" } },
        { id: "sort1", type: "sort", data: { field: "balance", direction: "desc" } },
        { id: "limit1", type: "limit", data: { n: 10 } },
        { id: "out1", type: "output_table", data: { title: "Top 10 holders" } },
      ],
      [
        { from: "holders1", to: "sort1" },
        { from: "sort1", to: "limit1" },
        { from: "limit1", to: "out1" },
      ],
    ),
  },
  {
    id: "top-100-holders",
    title: "Top 100 holders",
    blurb: "The same as Top 10, with First rows set to 100.",
    graph: chain(
      "Top 100 holders",
      [
        { id: "holders1", type: "source", data: { source: "holders" } },
        { id: "sort1", type: "sort", data: { field: "balance", direction: "desc" } },
        { id: "limit1", type: "limit", data: { n: 100 } },
        { id: "out1", type: "output_table", data: { title: "Top 100 holders" } },
      ],
      [
        { from: "holders1", to: "sort1" },
        { from: "sort1", to: "limit1" },
        { from: "limit1", to: "out1" },
      ],
    ),
  },
  {
    id: "wash-wallets",
    title: "Wash trading wallets",
    blurb: "One row per wash group. Turn on Show all wallets for every wallet.",
    graph: chain(
      "Wash trading wallets",
      [
        { id: "swaps1", type: "source", data: { source: "swaps" } },
        { id: "transfers1", type: "source", data: { source: "transfers" } },
        { id: "detect1", type: "detect_cycles", data: {} },
        { id: "out1", type: "output_table", data: { title: "Wash groups" } },
      ],
      [
        { from: "swaps1", to: "detect1", sourceHandle: "out", targetHandle: "in_swaps" },
        { from: "transfers1", to: "detect1", sourceHandle: "out", targetHandle: "in_transfers" },
        { from: "detect1", to: "out1", sourceHandle: "out_wallets", targetHandle: "in" },
      ],
    ),
  },
  {
    id: "wash-percent",
    title: "Wash volume % of total volume",
    blurb: "Flagged swap dollars divided by all swap dollars.",
    graph: chain(
      "Wash volume percent",
      [
        { id: "swaps1", type: "source", data: { source: "swaps" } },
        { id: "transfers1", type: "source", data: { source: "transfers" } },
        { id: "detect1", type: "detect_cycles", data: {} },
        { id: "filter1", type: "condition", data: { mode: "compare", field: "is_wash", op: "=", value: true } },
        { id: "ratio1", type: "ratio", data: { field: "usd_value" } },
        { id: "out1", type: "output_number", data: { title: "Wash volume", format: "percent" } },
      ],
      [
        { from: "swaps1", to: "detect1", sourceHandle: "out", targetHandle: "in_swaps" },
        { from: "transfers1", to: "detect1", sourceHandle: "out", targetHandle: "in_transfers" },
        { from: "detect1", to: "filter1", sourceHandle: "out_swaps", targetHandle: "in" },
        { from: "filter1", to: "ratio1", sourceHandle: "out", targetHandle: "in_part" },
        { from: "swaps1", to: "ratio1", sourceHandle: "out", targetHandle: "in_whole" },
        { from: "ratio1", to: "out1" },
      ],
    ),
  },
  {
    id: "biggest-buyers",
    title: "Biggest buyers",
    blurb: "Wallets that spent the most buying this token. Change 10 in First rows.",
    graph: chain(
      "Biggest buyers",
      [
        { id: "wallets1", type: "source", data: { source: "wallets" } },
        { id: "sort1", type: "sort", data: { field: "buy_volume_usd", direction: "desc" } },
        { id: "limit1", type: "limit", data: { n: 10 } },
        { id: "out1", type: "output_table", data: { title: "Biggest buyers" } },
      ],
      [
        { from: "wallets1", to: "sort1" },
        { from: "sort1", to: "limit1" },
        { from: "limit1", to: "out1" },
      ],
    ),
  },
  {
    id: "biggest-sellers",
    title: "Biggest sellers",
    blurb: "Wallets that received the most selling this token.",
    graph: chain(
      "Biggest sellers",
      [
        { id: "wallets1", type: "source", data: { source: "wallets" } },
        { id: "sort1", type: "sort", data: { field: "sell_volume_usd", direction: "desc" } },
        { id: "limit1", type: "limit", data: { n: 10 } },
        { id: "out1", type: "output_table", data: { title: "Biggest sellers" } },
      ],
      [
        { from: "wallets1", to: "sort1" },
        { from: "sort1", to: "limit1" },
        { from: "limit1", to: "out1" },
      ],
    ),
  },
  {
    id: "never-sold",
    title: "Wallets that never sold",
    blurb: "Still holding, with no sell in the loaded window.",
    graph: chain(
      "Never sold",
      [
        { id: "holders1", type: "source", data: { source: "holders" } },
        { id: "filter1", type: "condition", data: { mode: "compare", field: "ever_sold", op: "=", value: false } },
        { id: "out1", type: "output_table", data: { title: "Never sold" } },
      ],
      [
        { from: "holders1", to: "filter1" },
        { from: "filter1", to: "out1" },
      ],
    ),
  },
  {
    id: "long-term",
    title: "Long-term holders",
    blurb: "Held more than 30 days. Load a long enough window, then change 30 if you want.",
    graph: chain(
      "Long-term holders",
      [
        { id: "holders1", type: "source", data: { source: "holders" } },
        { id: "filter1", type: "condition", data: { mode: "compare", field: "held_days", op: ">", value: 30 } },
        { id: "out1", type: "output_table", data: { title: "Long-term holders" } },
      ],
      [
        { from: "holders1", to: "filter1" },
        { from: "filter1", to: "out1" },
      ],
    ),
  },
  {
    id: "daily-volume",
    title: "Daily volume chart",
    blurb: "Swap dollars by day. Add Find wash groups in front if you also want the wash series.",
    graph: chain(
      "Daily volume",
      [
        { id: "swaps1", type: "source", data: { source: "swaps" } },
        { id: "group1", type: "group_by", data: { by: "day" } },
        { id: "out1", type: "output_chart", data: { title: "Daily volume" } },
      ],
      [
        { from: "swaps1", to: "group1" },
        { from: "group1", to: "out1" },
      ],
    ),
  },
  {
    id: "unusual",
    title: "Unusual trades",
    blurb: "Swaps that look least like the bulk of past trading. Change 20 in First rows.",
    graph: chain(
      "Unusual trades",
      [
        { id: "swaps1", type: "source", data: { source: "swaps" } },
        { id: "score1", type: "anomaly_score", data: {} },
        { id: "sort1", type: "sort", data: { field: "anomaly_score", direction: "desc" } },
        { id: "limit1", type: "limit", data: { n: 20 } },
        { id: "out1", type: "output_table", data: { title: "Unusual trades" } },
      ],
      [
        { from: "swaps1", to: "score1" },
        { from: "score1", to: "sort1" },
        { from: "sort1", to: "limit1" },
        { from: "limit1", to: "out1" },
      ],
    ),
  },
  {
    id: "concentration",
    title: "How concentrated is ownership?",
    blurb: "Share held by the biggest wallets, and a Gini score. Change how many in the box.",
    graph: chain(
      "How concentrated is ownership?",
      [
        { id: "q1", type: "concentration", data: { top_n: 10 } },
        { id: "out1", type: "output_table", data: { title: "Holder concentration" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
  {
    id: "pnl",
    title: "Who made or lost money?",
    blurb: "Top winners and losers. Cost stays blank when the buy was before this window.",
    graph: chain(
      "Who made or lost money?",
      [
        { id: "q1", type: "pnl", data: { top_n: 10 } },
        { id: "out1", type: "output_table", data: { title: "Profit and loss" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
  {
    id: "sandwich",
    title: "Sandwich attacks",
    blurb: "Same wallet, same block, same pool, around someone else's swap.",
    graph: chain(
      "Sandwich attacks",
      [
        { id: "q1", type: "sandwich", data: {} },
        { id: "out1", type: "output_table", data: { title: "Sandwich attacks" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
  {
    id: "insiders",
    title: "Insiders who sold",
    blurb: "Got the token from the creator, then sold. Paste a creator if the box cannot find one.",
    graph: chain(
      "Insiders who sold",
      [
        { id: "q1", type: "insiders", data: { creator: "" } },
        { id: "out1", type: "output_table", data: { title: "Insiders" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
  {
    id: "fake-holders",
    title: "Fake holders",
    blurb: "At least 5 wallets first funded by one source within 1 hour.",
    graph: chain(
      "Fake holders",
      [
        { id: "q1", type: "fake_holders", data: { minutes: 60, min_wallets: 5 } },
        { id: "out1", type: "output_table", data: { title: "Fake holders" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
  {
    id: "liquidity-pulls",
    title: "Liquidity pulls",
    blurb: "A pool lost at least 50% of the liquidity added here, within 1 hour.",
    graph: chain(
      "Liquidity pulls",
      [
        { id: "q1", type: "liquidity_pull", data: { minutes: 60, percent: 50 } },
        { id: "out1", type: "output_table", data: { title: "Liquidity pulls" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
  {
    id: "whales",
    title: "Whale moves",
    blurb: "Transfers of at least $100,000 or 1% of the balances we could read.",
    graph: chain(
      "Whale moves",
      [
        { id: "q1", type: "whales", data: { usd: 100000, percent: 1 } },
        { id: "out1", type: "output_table", data: { title: "Whale moves" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
  {
    id: "pressure",
    title: "Buy vs sell pressure",
    blurb: "Each hour's dollars bought and sold, with the price under the bars.",
    graph: chain(
      "Buy vs sell pressure",
      [
        { id: "q1", type: "pressure", data: {} },
        { id: "out1", type: "output_chart", data: { title: "Buy vs sell pressure" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
  {
    id: "bots",
    title: "Bots vs humans",
    blurb: "A bot or person label, and the reason, for busy wallets.",
    graph: chain(
      "Bots vs humans",
      [
        { id: "q1", type: "bots", data: { top_n: 10 } },
        { id: "out1", type: "output_table", data: { title: "Bots vs humans" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
  {
    id: "wash-over-time",
    title: "Wash share over time",
    blurb: "Each day's flagged wash dollars as a percent of that day's volume.",
    graph: chain(
      "Wash share over time",
      [
        { id: "q1", type: "wash_over_time", data: {} },
        { id: "out1", type: "output_chart", data: { title: "Wash share over time" } },
      ],
      [{ from: "q1", to: "out1" }],
    ),
  },
];
