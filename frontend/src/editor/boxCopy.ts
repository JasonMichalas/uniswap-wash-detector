// Display only. A block is about 12 seconds; the stored setting stays in blocks.
const BLOCK_SECONDS = 12;

const SOURCE_TITLE: Record<string, string> = {
  swaps: "Swaps",
  transfers: "Transfers",
  wallets: "Wallets",
  holders: "Holders",
  pools: "Pools",
};

const TITLE: Record<string, string> = {
  condition: "Keep rows",
  detect_cycles: "Find wash groups",
  ml_score: "Wash score",
  anomaly_score: "Unusual trades",
  explain: "Score reasons",
  aggregate: "One number",
  limit: "First rows",
};

const TECHNICAL: Record<string, string> = {
  condition: "Condition",
  detect_cycles: "Cycle detector",
  ml_score: "ML wash score",
  anomaly_score: "Anomaly score",
  explain: "Explain",
  aggregate: "Aggregate",
  limit: "Limit",
};

const FIELD_WORDS: Record<string, string> = {
  block_number: "block number",
  trader: "trader",
  side: "buy or sell",
  pool_address: "pool",
  protocol: "protocol",
  usd_value: "dollar size",
  amount_token: "token amount",
  tx_hash: "transaction",
  from_address: "sender",
  to_address: "receiver",
  amount: "amount",
  wallet: "wallet",
  balance: "balance",
  held_days: "days held",
  held_days_is_lower_bound: "days held is a minimum",
  acquired_before_window: "bought before the window",
  ever_sold: "ever sold",
  days_since_sell: "days since a sale",
  swap_count: "number of swaps",
  buy_volume_usd: "buy volume in dollars",
  sell_volume_usd: "sell volume in dollars",
  confidence: "confidence",
  wash_volume_usd: "wash volume in dollars",
  round_trip_count: "round trips",
  ring_count: "rings",
  net_ratio: "how far buys and sells differ",
  volume_usd: "volume in dollars",
  cycle_id: "group id",
  size: "group size",
  kind: "kind",
  pattern_type: "pattern",
  n_buyers: "number of buyers",
  n_sellers: "number of sellers",
  n_wallets: "number of wallets",
  wallets: "wallets",
  start_block: "start block",
  end_block: "end block",
  token0_symbol: "token 0",
  token1_symbol: "token 1",
  fee: "fee",
  rank: "rank",
  feature: "reason",
  feature_value: "reason value",
  shap_value: "how much this reason moved the score",
  direction: "direction",
  value: "value",
  day: "day",
  count: "count",
  sum_usd_value: "dollar volume",
  sum_wash_usd: "wash volume in dollars",
  ml_score: "wash score",
  ml_weighted_usd: "score times dollar size",
  is_wash: "marked as wash",
};

const OP_WORDS: Record<string, string> = {
  ">": "above",
  ">=": "at least",
  "<": "below",
  "<=": "at most",
  "=": "equal to",
  "!=": "not equal to",
};

const PORTS: Record<string, { label: string; tip: string }> = {
  "source:out": { label: "rows", tip: "The table. Connect this to a filter, a detector, or a chart." },
  "condition:in": { label: "rows", tip: "Any table. Rows that fail the test are dropped." },
  "condition:out": { label: "kept rows", tip: "The rows that passed. Connect this to the next box." },
  "time_window:in": { label: "trades", tip: "Swaps or transfers from the loaded window." },
  "time_window:out": { label: "kept rows", tip: "The same trades, cut down to the last few days." },
  "detect_cycles:in_swaps": {
    label: "swaps",
    tip: "Connect the Swaps box. These are the trades the search looks through.",
  },
  "detect_cycles:in_transfers": {
    label: "transfers",
    tip: "Connect the Transfers box. Transfers help tie wallets together.",
  },
  "detect_cycles:out_swaps": { label: "wash swaps", tip: "The trades that belong to a wash group." },
  "detect_cycles:out_wallets": { label: "wallets", tip: "The wallets that were in a wash group." },
  "detect_cycles:out_cycles": { label: "groups", tip: "One row per wash group, with its size and pattern." },
  "ml_score:in": { label: "swaps or wallets", tip: "Each swap or wallet gets a wash score from 0 to 1." },
  "ml_score:out": {
    label: "scored",
    tip: "The same rows, with a wash score on each. Connect this to Percent of to get a share of volume.",
  },
  "anomaly_score:in": { label: "swaps or wallets", tip: "Each swap or wallet gets a score for how unusual it looks." },
  "anomaly_score:out": { label: "scored", tip: "The same rows, plus an unusual-activity score." },
  "explain:in": { label: "wallets", tip: "Wallets to explain, usually the highest wash scores." },
  "explain:out": { label: "reasons", tip: "The main reasons for each wallet's score. Connect this to a table." },
  "group_by:in": { label: "rows", tip: "Swaps, transfers, wash wallets, or pools." },
  "group_by:out": { label: "groups", tip: "One row per day, wallet, or pool, with the dollars added up." },
  "aggregate:in": { label: "rows", tip: "The rows to count or add up." },
  "aggregate:out": { label: "number", tip: "A single number. Connect this to Show a number, or to Percent of." },
  "ratio:in_part": {
    label: "part",
    tip: "The piece you want as a percent. For a wash share, use the scored output.",
  },
  "ratio:in_whole": { label: "whole", tip: "The full set you divide by, usually the same scored rows." },
  "ratio:out": { label: "percent", tip: "One number. Connect this to Show a number." },
  "sort:in": { label: "rows", tip: "The rows to order." },
  "sort:out": { label: "sorted", tip: "The same rows, largest or smallest first. Connect this to First rows for a top list." },
  "limit:in": { label: "rows", tip: "Usually the sorted rows, so the first ones are the ones you care about." },
  "limit:out": { label: "kept rows", tip: "Only the first N rows." },
  "output_number:in": { label: "number", tip: "A single value, from One number or Percent of." },
  "output_table:in": { label: "rows", tip: "Any table. It shows up under Result after you press Run." },
  "output_chart:in": { label: "by day", tip: "A Group by day result. Wash swaps add a second bar." },
};

const MISSING: Record<string, string> = {
  "ratio:part":
    "Connect something to the part input of Percent of — usually the wash score's scored output.",
  "ratio:whole":
    "Connect the full set to the whole input of Percent of — usually the same scored rows, so there is a total to divide by.",
  "ml_score:swaps or wallets":
    "Connect swaps or wallets to Wash score. The Swaps box is the usual choice.",
  "anomaly_score:swaps or wallets":
    "Connect swaps or wallets to Unusual trades. The Swaps box is the usual choice.",
  "detect_cycles:swaps": "Connect the Swaps box to the swaps input of Find wash groups.",
  "detect_cycles:transfers": "Connect the Transfers box to the transfers input of Find wash groups.",
  "explain:wallets": "Connect wallets to Score reasons. The wallets output of Find wash groups is the usual choice.",
  "condition:in": "Connect a table to the rows input of Keep rows.",
  "time_window:in": "Connect swaps or transfers to the trades input of Time window.",
  "group_by:in": "Connect swaps, transfers, or wallets to the rows input of Group by.",
  "aggregate:in": "Connect the rows you want counted to One number.",
  "sort:in": "Connect a table to the rows input of Sort.",
  "limit:in": "Connect rows to First rows. Put Sort in front of it for a top list.",
  "output_number:value": "Connect a single number to Show a number. Percent of or One number both work.",
  "output_table:rows": "Connect a table of rows to Show a table.",
  "output_chart:groups": "Connect a Group by day result to Show a chart.",
};

export const TOLERANCE_MIN = 0.01;
export const TOLERANCE_MAX = 0.2;
export const TOLERANCE_STEP = 0.01;

export const WINDOW_PRESETS: { blocks: number; label: string }[] = [
  { blocks: 300, label: "about 1 hour" },
  { blocks: 1800, label: "about 6 hours" },
  { blocks: 7200, label: "about 1 day" },
  { blocks: 21600, label: "about 3 days" },
  { blocks: 50400, label: "about 7 days" },
  { blocks: 100000, label: "the whole loaded window" },
];

export const GROUP_SIZE_PRESETS = [2, 4, 8, 16, 24, 32, 64];

export const WASH_RULES: { id: string; name: string }[] = [
  { id: "R1", name: "Same wallet buys and sells" },
  { id: "R2", name: "Tokens passed along, then sold" },
  { id: "R3", name: "Wallets funded or paid together" },
  { id: "R4", name: "A circle of wallets" },
  { id: "R5", name: "Many trades, nothing left" },
  { id: "R6", name: "Buy and sell in one transaction" },
];

export function boxTitle(nodeType: string, params: Record<string, unknown>): string {
  if (nodeType === "source") return SOURCE_TITLE[String(params.source ?? "")] ?? "Table";
  return TITLE[nodeType] ?? fallbackTitle(nodeType);
}

export function technicalName(nodeType: string): string {
  return TECHNICAL[nodeType] ?? "";
}

export function faceLine(nodeType: string, params: Record<string, unknown>): string {
  return line(nodeType, params, false);
}

export function whatItDoes(nodeType: string, params: Record<string, unknown>): string {
  return line(nodeType, params, true);
}

export function howToUse(nodeType: string): string {
  const lines: Record<string, string> = {
    source: "Connect the rows output to a filter, Find wash groups, a wash score, or a chart.",
    condition: "Connect any table to rows. Connect kept rows onward, for example to a table or a count.",
    time_window: "Connect swaps or transfers in. This only shortens the range you already loaded.",
    detect_cycles:
      "Connect Swaps to swaps and Transfers to transfers. Tick which kinds of wash trading to look for, then use wash swaps, wallets, or groups.",
    ml_score:
      "Connect swaps or wallets in. To turn the scores into a share of volume, connect scored to the part input of Percent of.",
    anomaly_score: "Connect swaps or wallets in. The scored output adds an unusual-activity score.",
    explain: "Connect wallets in, ideally after sorting the highest scores to the top. Connect reasons to a table.",
    group_by: "Connect swaps, transfers, wash wallets, or pools. Connect groups to a chart or a table.",
    aggregate: "Connect the rows you want counted. Connect the number to Show a number, or to Percent of.",
    ratio: "Connect the piece you care about to part, and the full set to whole. Connect percent to Show a number.",
    sort: "Connect rows in. Put First rows after this if you want a top list.",
    limit: "Connect sorted rows in so the first ones are the ones you care about. Connect kept rows to a table.",
    output_number: "Connect a single number, from One number or Percent of. The figure shows under Result after Run.",
    output_table: "Connect any rows. The table shows under Result after Run.",
    output_chart: "Connect a Group by day result. If the swaps were flagged as wash, wash volume is a second series.",
  };
  return lines[nodeType] ?? "Connect an input, then connect the output to the next box.";
}

export function portLabel(nodeType: string, socketId: string, fallback: string): string {
  return PORTS[`${nodeType}:${socketId}`]?.label ?? fallback;
}

export function portTip(nodeType: string, socketId: string, fallback: string): string {
  return PORTS[`${nodeType}:${socketId}`]?.tip ?? `Connect ${fallback} here.`;
}

export function fieldWords(name: string): string {
  if (!name) return "a column";
  return FIELD_WORDS[name] ?? name.replaceAll("_", " ");
}

export function opWords(op: string): string {
  return OP_WORDS[op] ?? op;
}

export function settingLabel(nodeType: string, name: string): string {
  const labels: Record<string, Record<string, string>> = {
    condition: {
      mode: "What to test",
      field: "Column",
      op: "Comparison",
      value: "Value",
      days: "How many days",
      negate: "Only wallets that did not sell",
    },
    time_window: { days: "How many days to keep" },
    detect_cycles: {
      tolerance: "How close buys and sells must be",
      window_blocks: "Longest time between buy and sell",
      max_group_size: "Most wallets in a chain",
      rules: "Which kinds of wash trading",
    },
    group_by: { by: "One row per" },
    aggregate: { fn: "What to calculate", field: "Column" },
    ratio: { field: "Column to add up", over: "Divide by" },
    sort: { field: "Column", direction: "Order" },
    limit: { n: "How many rows" },
    output_number: { title: "Heading", format: "How to show it" },
    output_table: { title: "Heading" },
    output_chart: { title: "Heading" },
  };
  return labels[nodeType]?.[name] ?? name;
}

export function settingHint(nodeType: string, name: string, params: Record<string, unknown>): string {
  if (nodeType === "detect_cycles" && name === "tolerance") {
    return `How close buys and sells must be to count as wash. 5% means 4 bought vs 4.01 sold counts. This box uses ${percentText(numberParam(params.tolerance, 0.05))}.`;
  }
  if (nodeType === "detect_cycles" && name === "window_blocks") {
    const blocks = numberParam(params.window_blocks, 7200);
    return `Longest time between a buy and the sell that uses those tokens: ${blocks} blocks, ${windowPhrase(blocks)}. A new block lands about every 12 seconds.`;
  }
  if (nodeType === "detect_cycles" && name === "max_group_size") {
    return "Most wallets in one chain. A 20-hop pass needs room for 21 wallets. Bigger chains take longer.";
  }
  if (nodeType === "detect_cycles" && name === "rules") {
    return `Which kinds of wash trading this box looks for: ${rulePhrase(params.rules)}.`;
  }
  const hints: Record<string, Record<string, string>> = {
    condition: {
      mode: "Compare a column, or keep wallets based on whether they sold recently.",
      field: "The column the test looks at.",
      op: "How the column compares to the value.",
      value: "Rows stay when this comparison is true.",
      days: "How far back to look for a sale.",
      negate: "On keeps wallets that did not sell in that time.",
    },
    time_window: { days: "Only this many days at the end of the loaded range are kept." },
    group_by: { by: "Each group becomes one row, with the dollar volume added up." },
    aggregate: {
      fn: "Count the rows, add a column, or average a column.",
      field: "The column to add or average.",
    },
    ratio: {
      field: "The column to add up on the part side. For a wash share, use score times dollar size.",
      over: "The column on the whole side. Leave this blank to use the same column on both.",
    },
    sort: { field: "The column to order by.", direction: "Largest first, or smallest first." },
    limit: { n: "How many rows to keep from the top of the list." },
    output_number: {
      title: "The heading on the result.",
      format: "Percent adds a % sign. Whole number drops the decimals.",
    },
    output_table: { title: "The heading on the result." },
    output_chart: { title: "The heading on the result." },
  };
  return hints[nodeType]?.[name] ?? "";
}

export function windowPhrase(blocks: number): string {
  const minutes = (blocks * BLOCK_SECONDS) / 60;
  if (!Number.isFinite(minutes) || minutes <= 0) return "an unknown length of time";
  if (minutes < 90) {
    const n = Math.max(1, Math.round(minutes));
    return n === 1 ? "about 1 minute" : `about ${n} minutes`;
  }
  const hours = minutes / 60;
  if (hours < 36) {
    const n = Math.round(hours);
    return n === 1 ? "about 1 hour" : `about ${n} hours`;
  }
  const days = Math.max(1, Math.round(hours / 24));
  return days === 1 ? "about 1 day" : `about ${days} days`;
}

export function describeProblem(message: string, nodeType: string | undefined): string {
  const missing = message.match(/missing a wire into '([^']+)'/);
  if (missing) {
    const socket = missing[1];
    return (
      MISSING[`${nodeType ?? ""}:${socket}`] ??
      `Connect a box to the ${socket} input of ${boxTitle(nodeType ?? "", {})}.`
    );
  }
  const many = message.match(/has (\d+) wires into '([^']+)'/);
  if (many) {
    const title = boxTitle(nodeType ?? "", {});
    return `${title} has ${many[1]} wires into ${many[2]}. Keep one.`;
  }
  if (message.includes("graph has a loop") || message.includes("have to be acyclic")) {
    return "Two boxes are wired in a circle. Wash groups come from Find wash groups. A box cannot feed back into itself.";
  }
  if (message.startsWith("Cannot connect")) return softenNames(message);
  return softenNames(message);
}

function line(nodeType: string, params: Record<string, unknown>, full: boolean): string {
  if (nodeType === "source") return sourceLine(String(params.source ?? ""), full);
  if (nodeType === "condition") return conditionLine(params);
  if (nodeType === "time_window") {
    const days = params.days ?? "?";
    return `Keeps only the last ${days} days of swaps or transfers from the range you loaded.`;
  }
  if (nodeType === "detect_cycles") return cycleLine(params, full);
  if (nodeType === "ml_score") {
    const base = "Gives each swap or wallet a score from 0 to 1 for how much it looks like wash trading.";
    return full ? `${base} Higher means more like the wash examples this score was built from.` : base;
  }
  if (nodeType === "anomaly_score") {
    const base = "Gives each swap or wallet a score for how unusual it looks next to ordinary trading.";
    return full ? `${base} Higher means more unusual. It is not a chance that the trade is wash.` : base;
  }
  if (nodeType === "explain") {
    return "Lists the main reasons behind the wash score, for up to 25 wallets.";
  }
  if (nodeType === "group_by") {
    const by = String(params.by ?? "day");
    const noun = by === "wallet" ? "wallet" : by === "pool" ? "pool" : "day";
    return `Adds up dollar volume for each ${noun}.`;
  }
  if (nodeType === "aggregate") return aggregateLine(params);
  if (nodeType === "ratio") return ratioLine(params, full);
  if (nodeType === "sort") return sortLine(params);
  if (nodeType === "limit") return `Keeps the first ${params.n ?? "?"} rows.`;
  if (nodeType === "output_number") {
    const title = textParam(params.title, "Result");
    const format = String(params.format ?? "number");
    const as = format === "percent" ? " as a percent" : format === "integer" ? " as a whole number" : "";
    return `Shows one number titled ${title}${as}.`;
  }
  if (nodeType === "output_table") return `Shows the rows in a table titled ${textParam(params.title, "Results")}.`;
  if (nodeType === "output_chart") {
    return `Draws daily bars titled ${textParam(params.title, "Volume")}. Wash volume is a second series when the swaps were flagged.`;
  }
  return "This box changes the rows connected to it.";
}

function sourceLine(source: string, full: boolean): string {
  const lines: Record<string, [string, string]> = {
    swaps: [
      "The buys and sells in this token's pools for the window you loaded.",
      "The buys and sells in this token's pools for the window you loaded. This is the usual starting point.",
    ],
    transfers: [
      "Every transfer of this token in the loaded window, including ones that are not trades.",
      "Every transfer of this token in the loaded window, including ones that are not trades. Find wash groups uses these to tie wallets together.",
    ],
    wallets: [
      "Every wallet that showed up in the loaded window.",
      "Every wallet that showed up in the loaded window, whether or not it still holds the token.",
    ],
    holders: [
      "Wallets that still held this token at the end of the window.",
      "Wallets that still held this token at the end of the window. Balance is the amount they held.",
    ],
    pools: [
      "The Uniswap pools this token trades in, quoted in ETH or a stablecoin.",
      "The Uniswap pools this token trades in, quoted in ETH or a stablecoin. Each row is one pool.",
    ],
  };
  const pair = lines[source];
  if (!pair) return "A table from the loaded window. Pick which one in the panel.";
  return full ? pair[1] : pair[0];
}

function conditionLine(params: Record<string, unknown>): string {
  if (params.mode === "sold_in") {
    const days = params.days ?? "?";
    return params.negate
      ? `Keeps wallets that did not sell in the last ${days} days.`
      : `Keeps wallets that sold in the last ${days} days.`;
  }
  const field = String(params.field ?? "");
  const op = opWords(String(params.op ?? ">"));
  const value = formatValue(params.value);
  if (!field) return `Keeps rows where a column is ${op} ${value}. Pick the column on the right.`;
  return `Keeps rows where ${fieldWords(field)} is ${op} ${value}.`;
}

function cycleLine(params: Record<string, unknown>, full: boolean): string {
  const pct = percentText(numberParam(params.tolerance, 0.05));
  const when = windowPhrase(numberParam(params.window_blocks, 7200));
  const group = numberParam(params.max_group_size, 24);
  const kinds = rulePhrase(params.rules);
  const face = `Looks for ${kinds}. Buys and sells must match within ${pct}, no more than ${when} apart, in a chain of at most ${group} wallets.`;
  if (!full) return face;
  return `${face} A match can be one wallet, tokens passed along, wallets funded together, a circle, a flat burst of trades, or a buy and a sell in one transaction.`;
}

export function selectedRules(value: unknown): string[] {
  const text = typeof value === "string" ? value : "";
  const chosen = text
    .split(",")
    .map((part) => part.trim())
    .filter((part) => WASH_RULES.some((rule) => rule.id === part));
  return chosen.length ? chosen : WASH_RULES.map((rule) => rule.id);
}

function rulePhrase(value: unknown): string {
  const chosen = selectedRules(value);
  if (chosen.length === WASH_RULES.length) return "every kind of wash trading";
  const names = WASH_RULES.filter((rule) => chosen.includes(rule.id)).map((rule) => rule.name);
  if (names.length === 1) return names[0].toLowerCase();
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1].toLowerCase()}`;
}

function aggregateLine(params: Record<string, unknown>): string {
  const field = fieldWords(String(params.field ?? ""));
  if (params.fn === "sum") return `Adds up ${field}.`;
  if (params.fn === "avg") return `Averages ${field}.`;
  return "Counts the rows.";
}

function ratioLine(params: Record<string, unknown>, full: boolean): string {
  const field = fieldWords(String(params.field || "dollar size"));
  const over = String(params.over ?? "");
  const base = over
    ? `Shows 100 times ${field} on the part, divided by ${fieldWords(over)} on the whole.`
    : `Shows 100 times ${field} on the part, divided by the same column on the whole.`;
  if (!full) return base;
  return `${base} That is the part as a percent of the whole.`;
}

function sortLine(params: Record<string, unknown>): string {
  const field = String(params.field ?? "");
  const way = params.direction === "asc" ? "smallest" : "largest";
  if (!field) return `Puts the ${way} values first. Pick a column on the right.`;
  return `Puts the ${way} ${fieldWords(field)} first.`;
}

function fallbackTitle(nodeType: string): string {
  const names: Record<string, string> = {
    time_window: "Time window",
    group_by: "Group by",
    ratio: "Percent of",
    sort: "Sort",
    output_number: "Number",
    output_table: "Table",
    output_chart: "Chart",
  };
  return names[nodeType] ?? "Box";
}

function percentText(tolerance: number): string {
  const pct = tolerance * 100;
  const rounded = Math.round(pct * 10) / 10;
  const text = Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(1);
  return `${text}%`;
}

function numberParam(value: unknown, fallback: number): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function textParam(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function formatValue(value: unknown): string {
  if (value === true) return "yes";
  if (value === false) return "no";
  if (value === "" || value === undefined || value === null) return "?";
  return String(value);
}

function softenNames(message: string): string {
  return message
    .replaceAll("Cycle detector", "Find wash groups")
    .replaceAll("ML wash score", "Wash score")
    .replaceAll("Anomaly score", "Unusual trades");
}
