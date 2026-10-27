import type { TemplateSummary } from "../types";

export function ExampleMenu({
  templates,
  open,
  onToggle,
  onOpen,
  label = "Examples",
}: {
  templates: TemplateSummary[];
  open: boolean;
  onToggle: () => void;
  onOpen: (id: string) => void;
  label?: string;
}) {
  return (
    <div className="examples">
      <button type="button" aria-expanded={open} onClick={onToggle}>
        {label}
      </button>
      {open && (
        <ul>
          {templates.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => onOpen(item.id)}>
                <strong>{item.name}</strong>
                <span>{item.description}</span>
              </button>
            </li>
          ))}
          {templates.length === 0 && <li className="muted">No examples found.</li>}
        </ul>
      )}
    </div>
  );
}
