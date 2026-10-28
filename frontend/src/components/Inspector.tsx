import {
  groupKeysFor,
  inferOutputs,
  inputOnHandle,
  singleInput,
  sourceLabel,
  type BoxNode,
} from "../flow";
import {
  GROUP_SIZE_PRESETS,
  TOLERANCE_MAX,
  TOLERANCE_MIN,
  TOLERANCE_STEP,
  WASH_RULES,
  WINDOW_PRESETS,
  selectedRules,
  boxTitle,
  fieldWords,
  howToUse,
  opWords,
  settingHint,
  settingLabel,
  technicalName,
  whatItDoes,
  windowPhrase,
} from "../editor/boxCopy";
import type { Catalog, FieldDef } from "../types";
import type { Edge } from "@xyflow/react";

export function Inspector({
  catalog,
  node,
  nodes,
  edges,
  onChange,
  onDelete,
}: {
  catalog: Catalog;
  node: BoxNode | null;
  nodes: BoxNode[];
  edges: Edge[];
  onChange: (id: string, params: Record<string, unknown>, label?: string) => void;
  onDelete: (id: string) => void;
}) {
  if (!node) {
    return (
      <aside className="inspector">
        <h2>Inspector</h2>
        <p className="muted">Click a box to change it. Delete removes the selected box, or press Backspace.</p>
      </aside>
    );
  }
  const outputs = inferOutputs(nodes, edges, catalog);
  const input = singleInput(node, edges, outputs, catalog);
  const part = inputOnHandle(node, "in_part", edges, outputs);
  const whole = inputOnHandle(node, "in_whole", edges, outputs);
  const fields = fieldsFor(node.data.nodeType, input, part);
  const title = boxTitle(node.data.nodeType, node.data.params);
  const technical = technicalName(node.data.nodeType);

  function update(key: string, value: unknown) {
    const params = { ...node!.data.params, [key]: value };
    const label =
      node!.data.nodeType === "source" ? sourceLabel(catalog, "source", params) || node!.data.label : undefined;
    onChange(node!.id, params, label);
  }

  function hint(name: string): string {
    return settingHint(node!.data.nodeType, name, node!.data.params);
  }

  return (
    <aside className="inspector">
      <h2>{title}</h2>
      {technical && <p className="tech-name">{technical}</p>}
      {node.data.nodeType === "detect_cycles" && (
        <CycleSettings params={node.data.params} onUpdate={update} />
      )}
      <h3>What this box does</h3>
      <p>{whatItDoes(node.data.nodeType, node.data.params)}</p>
      <h3>How to use it</h3>
      <p className="muted">{howToUse(node.data.nodeType)}</p>
      {node.data.nodeType === "source" && (
        <label>
          Which table
          <select value={String(node.data.params.source ?? "")} onChange={(event) => update("source", event.target.value)}>
            {catalog.palette
              .filter((item) => item.node_type === "source")
              .map((item) => (
                <option key={String(item.data.source)} value={String(item.data.source)}>
                  {item.label}
                </option>
              ))}
          </select>
          <span className="hint">This is the data the rest of the boxes read.</span>
        </label>
      )}
      {node.data.nodeType === "condition" && (
        <ConditionFields node={node} fields={fields} onUpdate={update} />
      )}
      {node.data.nodeType === "time_window" && (
        <NumberField
          label={settingLabel("time_window", "days")}
          hint={hint("days")}
          value={node.data.params.days}
          min={1}
          max={90}
          onChange={(value) => update("days", value)}
        />
      )}
      {node.data.nodeType === "group_by" && (
        <label>
          {settingLabel("group_by", "by")}
          <select value={String(node.data.params.by ?? "")} onChange={(event) => update("by", event.target.value)}>
            {groupKeysFor(input?.type).map((key) => (
              <option key={key} value={key}>
                {groupWord(key)}
              </option>
            ))}
          </select>
          <span className="hint">
            {input && groupKeysFor(input.type).length === 0
              ? "This stream cannot be grouped. Use swaps, transfers, or wash wallets."
              : hint("by")}
          </span>
        </label>
      )}
      {node.data.nodeType === "aggregate" && (
        <>
          <label>
            {settingLabel("aggregate", "fn")}
            <select value={String(node.data.params.fn ?? "count")} onChange={(event) => update("fn", event.target.value)}>
              <option value="count">Count the rows</option>
              <option value="sum">Add up a column</option>
              <option value="avg">Average a column</option>
            </select>
            <span className="hint">{hint("fn")}</span>
          </label>
          {node.data.params.fn !== "count" && (
            <FieldSelect
              label={settingLabel("aggregate", "field")}
              hint={hint("field")}
              fields={fields.filter((field) => field.kind === "number")}
              value={String(node.data.params.field ?? "")}
              onChange={(value) => update("field", value)}
            />
          )}
        </>
      )}
      {node.data.nodeType === "ratio" && (
        <>
          <FieldSelect
            label={settingLabel("ratio", "field")}
            hint={hint("field")}
            fields={(part?.fields ?? fields).filter((field) => field.kind === "number")}
            value={String(node.data.params.field ?? "")}
            onChange={(value) => update("field", value)}
            allowFree
          />
          <FieldSelect
            label={settingLabel("ratio", "over")}
            hint={hint("over")}
            fields={(whole?.fields ?? []).filter((field) => field.kind === "number")}
            value={String(node.data.params.over ?? "")}
            onChange={(value) => update("over", value)}
          />
        </>
      )}
      {node.data.nodeType === "sort" && (
        <>
          <FieldSelect
            label={settingLabel("sort", "field")}
            hint={hint("field")}
            fields={fields}
            value={String(node.data.params.field ?? "")}
            onChange={(value) => update("field", value)}
          />
          <label>
            {settingLabel("sort", "direction")}
            <select
              value={String(node.data.params.direction ?? "desc")}
              onChange={(event) => update("direction", event.target.value)}
            >
              <option value="desc">Largest first</option>
              <option value="asc">Smallest first</option>
            </select>
            <span className="hint">{hint("direction")}</span>
          </label>
        </>
      )}
      {node.data.nodeType === "limit" && (
        <NumberField
          label={settingLabel("limit", "n")}
          hint={hint("n")}
          value={node.data.params.n}
          min={1}
          max={1000}
          onChange={(value) => update("n", value)}
        />
      )}
      {node.data.nodeType === "output_number" && (
        <>
          <TextField
            label={settingLabel("output_number", "title")}
            hint={hint("title")}
            value={node.data.params.title}
            onChange={(value) => update("title", value)}
          />
          <label>
            {settingLabel("output_number", "format")}
            <select
              value={String(node.data.params.format ?? "number")}
              onChange={(event) => update("format", event.target.value)}
            >
              <option value="number">Plain number</option>
              <option value="percent">Percent</option>
              <option value="integer">Whole number</option>
            </select>
            <span className="hint">{hint("format")}</span>
          </label>
        </>
      )}
      {(node.data.nodeType === "output_table" || node.data.nodeType === "output_chart") && (
        <TextField
          label={settingLabel(node.data.nodeType, "title")}
          hint={hint("title")}
          value={node.data.params.title}
          onChange={(value) => update("title", value)}
        />
      )}
      <button type="button" className="ghost danger" onClick={() => onDelete(node.id)}>
        Remove box
      </button>
    </aside>
  );
}

function fieldsFor(nodeType: string, input: { fields: FieldDef[] } | null, part: { fields: FieldDef[] } | null): FieldDef[] {
  if (nodeType === "ratio") return part?.fields ?? [];
  return input?.fields ?? [];
}

function CycleSettings({
  params,
  onUpdate,
}: {
  params: Record<string, unknown>;
  onUpdate: (key: string, value: unknown) => void;
}) {
  const blocks = finite(params.window_blocks, 7200);
  const group = finite(params.max_group_size, 24);
  const windows = windowChoices(blocks);
  const groups = groupChoices(group);
  const chosen = new Set(selectedRules(params.rules));
  function toggleRule(id: string) {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    const ordered = WASH_RULES.filter((rule) => next.has(rule.id)).map((rule) => rule.id);
    onUpdate("rules", ordered.join(","));
  }
  return (
    <>
      <fieldset className="rule-kinds">
        <legend>{settingLabel("detect_cycles", "rules")}</legend>
        {WASH_RULES.map((rule) => (
          <label key={rule.id}>
            <input type="checkbox" checked={chosen.has(rule.id)} onChange={() => toggleRule(rule.id)} />
            {rule.name}
          </label>
        ))}
        <span className="hint">{settingHint("detect_cycles", "rules", params)}</span>
      </fieldset>
      <label>
        {settingLabel("detect_cycles", "tolerance")}
        <input
          type="range"
          min={TOLERANCE_MIN}
          max={TOLERANCE_MAX}
          step={TOLERANCE_STEP}
          value={finite(params.tolerance, 0.05)}
          onChange={(event) => onUpdate("tolerance", Number(event.target.value))}
        />
        <span className="hint">{settingHint("detect_cycles", "tolerance", params)}</span>
      </label>
      <label>
        {settingLabel("detect_cycles", "window_blocks")}
        <select value={String(blocks)} onChange={(event) => onUpdate("window_blocks", Number(event.target.value))}>
          {windows.map((option) => (
            <option key={option.blocks} value={option.blocks}>
              {option.label} ({option.blocks} blocks)
            </option>
          ))}
        </select>
        <span className="hint">{settingHint("detect_cycles", "window_blocks", params)}</span>
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={params.show_all === true}
          onChange={(event) => onUpdate("show_all", event.target.checked)}
        />
        Show all wallets
        <span className="hint">
          Off, the wallets table lists only wallets in a wash group. On, it also lists everyone else at 0%.
        </span>
      </label>
      <label>
        {settingLabel("detect_cycles", "max_group_size")}
        <select value={String(group)} onChange={(event) => onUpdate("max_group_size", Number(event.target.value))}>
          {groups.map((size) => (
            <option key={size} value={size}>
              {size} wallets
            </option>
          ))}
        </select>
        <span className="hint">{settingHint("detect_cycles", "max_group_size", params)}</span>
      </label>
    </>
  );
}

function windowChoices(current: number): { blocks: number; label: string }[] {
  const presets = WINDOW_PRESETS.map((item) => ({ ...item }));
  if (!presets.some((item) => item.blocks === current)) {
    presets.unshift({ blocks: current, label: windowPhrase(current) });
  }
  return presets;
}

function groupChoices(current: number): number[] {
  const values = new Set(GROUP_SIZE_PRESETS);
  values.add(current);
  return [...values].sort((a, b) => a - b);
}

function groupWord(key: string): string {
  if (key === "day") return "each day";
  if (key === "wallet") return "each wallet";
  if (key === "pool") return "each pool";
  return key;
}

function ConditionFields({
  node,
  fields,
  onUpdate,
}: {
  node: BoxNode;
  fields: FieldDef[];
  onUpdate: (key: string, value: unknown) => void;
}) {
  const mode = String(node.data.params.mode ?? "compare");
  const field = fields.find((item) => item.name === node.data.params.field);
  const ops = field?.kind === "number" ? [">", ">=", "<", "<=", "=", "!="] : ["=", "!="];
  const hint = (name: string) => settingHint("condition", name, node.data.params);
  return (
    <>
      <label>
        {settingLabel("condition", "mode")}
        <select value={mode} onChange={(event) => onUpdate("mode", event.target.value)}>
          <option value="compare">Compare a column</option>
          <option value="sold_in">Sold recently</option>
        </select>
        <span className="hint">{hint("mode")}</span>
      </label>
      {mode === "sold_in" ? (
        <>
          <NumberField
            label={settingLabel("condition", "days")}
            hint={hint("days")}
            value={node.data.params.days}
            min={1}
            max={365}
            onChange={(value) => onUpdate("days", value)}
          />
          <label className="check">
            <input
              type="checkbox"
              checked={Boolean(node.data.params.negate)}
              onChange={(event) => onUpdate("negate", event.target.checked)}
            />
            {settingLabel("condition", "negate")}
          </label>
          <span className="hint">{hint("negate")}</span>
        </>
      ) : (
        <>
          <FieldSelect
            label={settingLabel("condition", "field")}
            hint={hint("field")}
            fields={fields}
            value={String(node.data.params.field ?? "")}
            onChange={(value) => onUpdate("field", value)}
          />
          <label>
            {settingLabel("condition", "op")}
            <select value={String(node.data.params.op ?? "=")} onChange={(event) => onUpdate("op", event.target.value)}>
              {ops.map((op) => (
                <option key={op} value={op}>
                  {opWords(op)}
                </option>
              ))}
            </select>
            <span className="hint">{hint("op")}</span>
          </label>
          <ValueField
            kind={field?.kind}
            hint={hint("value")}
            value={node.data.params.value}
            onChange={(value) => onUpdate("value", value)}
          />
        </>
      )}
    </>
  );
}

function FieldSelect({
  label,
  hint,
  fields,
  value,
  onChange,
  allowFree = false,
}: {
  label: string;
  hint?: string;
  fields: FieldDef[];
  value: string;
  onChange: (value: string) => void;
  allowFree?: boolean;
}) {
  if (fields.length === 0) {
    return (
      <label>
        {label}
        {allowFree ? (
          <input value={value} onChange={(event) => onChange(event.target.value)} placeholder="dollar size" />
        ) : (
          <span className="hint">Connect an input first.</span>
        )}
        {hint && fields.length === 0 && allowFree ? <span className="hint">{hint}</span> : null}
      </label>
    );
  }
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Choose a column</option>
        {fields.map((field) => (
          <option key={field.name} value={field.name}>
            {fieldWords(field.name)}
          </option>
        ))}
      </select>
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

function ValueField({
  kind,
  hint,
  value,
  onChange,
}: {
  kind: FieldDef["kind"] | undefined;
  hint?: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (kind === "bool") {
    const current = value === true || value === "true" ? "true" : "false";
    return (
      <label>
        {settingLabel("condition", "value")}
        <select value={current} onChange={(event) => onChange(event.target.value === "true")}>
          <option value="true">yes</option>
          <option value="false">no</option>
        </select>
        {hint && <span className="hint">{hint}</span>}
      </label>
    );
  }
  if (kind === "number") {
    return (
      <label>
        {settingLabel("condition", "value")}
        <input
          type="number"
          value={typeof value === "number" ? value : ""}
          onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))}
        />
        {hint && <span className="hint">{hint}</span>}
      </label>
    );
  }
  return (
    <label>
      {settingLabel("condition", "value")}
      <input value={value === undefined || value === null ? "" : String(value)} onChange={(event) => onChange(event.target.value)} />
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

function NumberField({
  label,
  hint,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  hint?: string;
  value: unknown;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
}) {
  return (
    <label>
      {label}
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={typeof value === "number" ? value : ""}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (Number.isFinite(next)) onChange(next);
        }}
      />
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

function TextField({
  label,
  hint,
  value,
  onChange,
}: {
  label: string;
  hint?: string;
  value: unknown;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <input value={typeof value === "string" ? value : ""} onChange={(event) => onChange(event.target.value)} />
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

function finite(value: unknown, fallback: number): number {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : fallback;
}
