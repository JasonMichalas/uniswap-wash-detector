import { Handle, Position, type NodeProps } from "@xyflow/react";
import { boxTitle, faceLine, portLabel, portTip, technicalName } from "../editor/boxCopy";
import { nodeDef, type BoxNode } from "../flow";
import { useCatalog } from "./catalog";

export function BoxNodeView({ data }: NodeProps<BoxNode>) {
  const catalog = useCatalog();
  const def = nodeDef(catalog, data.nodeType);
  const inputs = def?.inputs ?? [];
  const outputs = def?.outputs ?? [];
  const title = boxTitle(data.nodeType, data.params);
  const technical = technicalName(data.nodeType);
  return (
    <article className={`box cat-${def?.category ?? "other"}`}>
      <header>
        <span className="cat">{friendlyCategory(def?.category)}</span>
        <strong>{title}</strong>
        {technical && <span className="tech">{technical}</span>}
      </header>
      <p className="summary">{faceLine(data.nodeType, data.params)}</p>
      {inputs.map((socket, index) => (
        <Handle
          key={socket.id}
          type="target"
          position={Position.Left}
          id={socket.id}
          style={{ top: socketTop(index, inputs.length) }}
          title={portTip(data.nodeType, socket.id, socket.label)}
        />
      ))}
      {outputs.map((socket, index) => (
        <Handle
          key={socket.id}
          type="source"
          position={Position.Right}
          id={socket.id}
          style={{ top: socketTop(index, outputs.length) }}
          title={portTip(data.nodeType, socket.id, socket.label)}
        />
      ))}
      <div className="sockets">
        <ul>
          {inputs.map((socket) => (
            <li key={socket.id} title={portTip(data.nodeType, socket.id, socket.label)}>
              {portLabel(data.nodeType, socket.id, socket.label)}
            </li>
          ))}
        </ul>
        <ul className="right">
          {outputs.map((socket) => (
            <li key={socket.id} title={portTip(data.nodeType, socket.id, socket.label)}>
              {portLabel(data.nodeType, socket.id, socket.label)}
            </li>
          ))}
        </ul>
      </div>
    </article>
  );
}

function friendlyCategory(category: string | undefined): string {
  if (category === "source") return "Data";
  if (category === "filter") return "Filter";
  if (category === "detector") return "Detect";
  if (category === "aggregate" || category === "arrange") return "Combine";
  if (category === "output") return "Show";
  return "Box";
}

function socketTop(index: number, count: number): string {
  if (count <= 1) return "72%";
  return `${58 + ((index + 1) / (count + 1)) * 34}%`;
}
