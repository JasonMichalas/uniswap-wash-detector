import { useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { boxTitle, faceLine, portLabel, portTip, technicalName, whatItDoes } from "../editor/boxCopy";
import { nodeDef, type BoxNode } from "../flow";
import { useCatalog } from "./catalog";

const TIP_DELAY_MS = 450;

export function BoxNodeView({ data }: NodeProps<BoxNode>) {
  const catalog = useCatalog();
  const def = nodeDef(catalog, data.nodeType);
  const inputs = def?.inputs ?? [];
  const outputs = def?.outputs ?? [];
  const title = boxTitle(data.nodeType, data.params);
  const technical = technicalName(data.nodeType);
  const articleRef = useRef<HTMLElement>(null);
  const timer = useRef<number | null>(null);
  const [open, setOpen] = useState(false);

  function arm() {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(true), TIP_DELAY_MS);
  }

  function dismiss() {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    setOpen(false);
  }

  return (
    <article
      ref={articleRef}
      className={`box cat-${def?.category ?? "other"}`}
      onMouseEnter={arm}
      onMouseLeave={dismiss}
      onPointerDown={dismiss}
    >
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
      {open && articleRef.current
        ? createPortal(
            <BoxTip anchor={articleRef.current} text={whatItDoes(data.nodeType, data.params)} />,
            document.body,
          )
        : null}
    </article>
  );
}

function BoxTip({ anchor, text }: { anchor: HTMLElement; text: string }) {
  const rect = anchor.getBoundingClientRect();
  const left = Math.max(8, Math.min(rect.left, window.innerWidth - 276));
  const top = Math.min(rect.bottom + 8, window.innerHeight - 72);
  return (
    <p className="box-tip" role="tooltip" style={{ left, top }}>
      {text}
    </p>
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
