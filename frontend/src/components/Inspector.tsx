import {
  groupKeysFor,
  inferOutputs,
  inputOnHandle,
  nodeDef,
  singleInput,
  sourceLabel,
  type BoxNode,
} from "../flow";
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
        <p className="muted">Select a box to edit its fields. Delete removes the selected box, or press Backspace.</p>
      </aside>
    );
  }
  const def = nodeDef(catalog, node.data.nodeType);
  const outputs = inferOutputs(nodes, edges, catalog);
  const input = singleInput(node, edges, outputs, catalog);
  const part = inputOnHandle(node, "in_part", edges, outputs);
  const fields = fieldsFor(node.data.nodeType, input, part);

  function update(key: string, value: unknown) {
    const params = { ...node!.data.params, [key]: value };
    const label =
      node!.data.nodeType === "source" ? sourceLabel(catalog, "source", params) || node!.data.label : undefined;
    onChange(node!.id, params, label);
  }

  return (
    <aside className="inspector">
      <h2>{node.data.label}</h2>
      <p className="muted">{def?.description}</p>
      {node.data.nodeType === "source" && (
        <label>
          Table
          <select value={String(node.data.params.source ?? "")} onChange={(event) => update("source", event.target.value)}>
            {catalog.palette
              .filter((item) => item.node_type === "source")
              .map((item) => (
                <option key={String(item.data.source)} value={String(item.data.source)}>
                  {item.label}
                </option>
              ))}
          </select>
        </label>
      )}
      {node.data.nodeType === "condition" && (
        <ConditionFields node={node} fields={fields} onUpdate={update} />
      )}
      {node.data.nodeType === "detect_cycles" && (
        <>
          <NumberField
            label="Amount tolerance (0–0.5)"
            value={node.data.params.tolerance ?? 0.05}
            min={0.001}
            max={0.5}
            step={0.01}
            onChange={(value) => update("tolerance", value)}
          />
          <NumberField
            label="Time window (blocks)"
            value={node.data.params.window_blocks ?? 200}
            min={1}
            max={50000}
            onChange={(value) => update("window_blocks", value)}
          />
          <NumberField
            label="Max wallets in a group"
            value={node.data.params.max_group_size ?? 16}
            min={2}
            max={64}
            onChange={(value) => update("max_group_size", value)}
          />
        </>
      )}
      {node.data.nodeType === "time_window" && (
        <NumberField
          label="Last N days of the loaded window"
          value={node.data.params.days}
          min={1}
          max={90}
          onChange={(value) => update("days", value)}
        />
      )}
      {node.data.nodeType === "group_by" && (
        <label>
          Group by
          <select value={String(node.data.params.by ?? "")} onChange={(event) => update("by", event.target.value)}>
            {groupKeysFor(input?.type).map((key) => (
              <option key={key} value={key}>
                {key}
              </option>
            ))}
          </select>
          {input && groupKeysFor(input.type).length === 0 && (
            <span className="hint">This stream cannot be grouped. Use swaps, transfers, or wash wallets.</span>
          )}
        </label>
      )}
      {node.data.nodeType === "aggregate" && (
        <>
          <label>
            Function
            <select value={String(node.data.params.fn ?? "count")} onChange={(event) => update("fn", event.target.value)}>
              <option value="count">count</option>
              <option value="sum">sum</option>
              <option value="avg">avg</option>
            </select>
          </label>
          {node.data.params.fn !== "count" && (
            <FieldSelect
              label="Field"
              fields={fields.filter((field) => field.kind === "number")}
              value={String(node.data.params.field ?? "")}
              onChange={(value) => update("field", value)}
            />
          )}
        </>
      )}
      {node.data.nodeType === "ratio" && (
        <FieldSelect
          label="Field"
          fields={(part?.fields ?? fields).filter((field) => field.kind === "number")}
          value={String(node.data.params.field ?? "")}
          onChange={(value) => update("field", value)}
          allowFree
        />
      )}
      {node.data.nodeType === "sort" && (
        <>
          <FieldSelect
            label="Field"
            fields={fields}
            value={String(node.data.params.field ?? "")}
            onChange={(value) => update("field", value)}
          />
          <label>
            Direction
            <select
              value={String(node.data.params.direction ?? "desc")}
              onChange={(event) => update("direction", event.target.value)}
            >
              <option value="desc">desc</option>
              <option value="asc">asc</option>
            </select>
          </label>
        </>
      )}
      {node.data.nodeType === "limit" && (
        <NumberField label="N" value={node.data.params.n} min={1} max={1000} onChange={(value) => update("n", value)} />
      )}
      {node.data.nodeType === "output_number" && (
        <>
          <TextField label="Title" value={node.data.params.title} onChange={(value) => update("title", value)} />
          <label>
            Format
            <select
              value={String(node.data.params.format ?? "number")}
              onChange={(event) => update("format", event.target.value)}
            >
              <option value="number">number</option>
              <option value="percent">percent</option>
              <option value="integer">integer</option>
            </select>
          </label>
        </>
      )}
      {(node.data.nodeType === "output_table" || node.data.nodeType === "output_chart") && (
        <TextField label="Title" value={node.data.params.title} onChange={(value) => update("title", value)} />
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
  return (
    <>
      <label>
        Mode
        <select value={mode} onChange={(event) => onUpdate("mode", event.target.value)}>
          <option value="compare">compare a field</option>
          <option value="sold_in">sold in the last N days</option>
        </select>
      </label>
      {mode === "sold_in" ? (
        <>
          <NumberField label="Days" value={node.data.params.days} min={1} max={365} onChange={(value) => onUpdate("days", value)} />
          <label className="check">
            <input
              type="checkbox"
              checked={Boolean(node.data.params.negate)}
              onChange={(event) => onUpdate("negate", event.target.checked)}
            />
            Has not sold
          </label>
        </>
      ) : (
        <>
          <FieldSelect
            label="Field"
            fields={fields}
            value={String(node.data.params.field ?? "")}
            onChange={(value) => onUpdate("field", value)}
          />
          <label>
            Operator
            <select value={String(node.data.params.op ?? "=")} onChange={(event) => onUpdate("op", event.target.value)}>
              {ops.map((op) => (
                <option key={op} value={op}>
                  {op}
                </option>
              ))}
            </select>
          </label>
          <ValueField kind={field?.kind} value={node.data.params.value} onChange={(value) => onUpdate("value", value)} />
        </>
      )}
    </>
  );
}

function FieldSelect({
  label,
  fields,
  value,
  onChange,
  allowFree = false,
}: {
  label: string;
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
          <input value={value} onChange={(event) => onChange(event.target.value)} placeholder="usd_value" />
        ) : (
          <span className="hint">Connect an input first.</span>
        )}
      </label>
    );
  }
  return (
    <label>
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">Choose a field</option>
        {fields.map((field) => (
          <option key={field.name} value={field.name}>
            {field.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function ValueField({
  kind,
  value,
  onChange,
}: {
  kind: FieldDef["kind"] | undefined;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (kind === "bool") {
    const current = value === true || value === "true" ? "true" : "false";
    return (
      <label>
        Value
        <select value={current} onChange={(event) => onChange(event.target.value === "true")}>
          <option value="true">true</option>
          <option value="false">false</option>
        </select>
      </label>
    );
  }
  if (kind === "number") {
    return (
      <label>
        Value
        <input
          type="number"
          value={typeof value === "number" ? value : ""}
          onChange={(event) => onChange(event.target.value === "" ? "" : Number(event.target.value))}
        />
      </label>
    );
  }
  return (
    <label>
      Value
      <input value={value === undefined || value === null ? "" : String(value)} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function NumberField({
  label,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
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
    </label>
  );
}

function TextField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <input value={typeof value === "string" ? value : ""} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}
